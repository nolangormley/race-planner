"""Shared LLM provider-calling helper.

Previously this logic (build a payload for whichever of groq/deepseek/gemini
is configured, POST it, unwrap the response) was independently copy-pasted
three times across main.py (get_ai_insight, get_ai_training_plan, and
training_plan_chat's inline provider branches) — ~300 near-identical lines
with small, undetected drift between the copies (e.g. only two of the three
had a request timeout). There is now exactly one place that knows how to
call each provider. See agent_prompting/improvement_plan.md Section 3/6.
"""
import os
import re
import threading
import time

import requests

# Shared anti-hallucination guardrails. Previously these existed only in
# get_ai_insight's system prompt (the dashboard's "Coach AI Insight" card,
# the one screenshotted in the project's own vp_review3.md/vp_review4.md) —
# get_ai_training_plan (the race-schedule generator) and training_plan_chat
# (the plan-editing chat) each had independent, hand-written prompts with no
# CTL/TSB reference bands and no anti-pseudoscience clause, so the same
# model could still hallucinate through either of those two surfaces even
# after the dashboard card was fixed. Centralized here so all three prompts
# draw from one place. See agent_prompting/improvement_plan.md Section 3.
TRAINING_BENCHMARKS = (
    "Use these rigid scientific benchmarks for context:\n"
    "- CTL: <40 Developing, 40-70 Consistent, 70-100 Advanced, >100 Elite Pro.\n"
    "- TSB: >15 Fresh (Peaking), -30 to -10 Optimal Training, <-30 High Overload.\n"
)

NO_PSEUDOSCIENCE_CLAUSE = (
    "Do not hallucinate pseudo-science. Running depletes energy. Rest restores it. "
)

NEVER_CONTRADICT_STANDING_CLAUSE = (
    "NEVER contradict the user's current 'Standing' provided in the prompt. "
)

PROVIDER_CONFIG = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key_env": "GROQ_API_KEY",
        "label": "Groq",
        "default_model": "llama-3.1-8b-instant",
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "key_env": "DEEPSEEK_API_KEY",
        "label": "Deepseek",
        "default_model": "deepseek-chat",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key_env": "GEMINI_API_KEY",
        "label": "Gemini",
        "default_model": "gemini-2.5-flash",
    },
}

LOCAL_LM_STUDIO_URL = "http://localhost:1234/api/v1/chat"
LOCAL_LM_STUDIO_MODEL = "mistralai/ministral-3-3b"


class LLMConfigError(Exception):
    """Raised for a configuration/response problem the caller should turn
    into its own fallback (a plain string, an {"error": ...} dict, a chat
    message, etc.) rather than a generic crash — what "handle this
    gracefully" means differs by call site, so this module doesn't decide
    that part."""
    pass


def get_llm_provider() -> str:
    return os.getenv("LLM_PROVIDER", "local").lower()


def call_llm(messages, model=None, json_mode=False, max_tokens=None, timeout=60, provider=None):
    """Calls the configured LLM provider (or an explicit override) with a
    full OpenAI-style `messages` list ([{"role": ..., "content": ...}, ...])
    and returns the text content of the first choice.

    Raises LLMConfigError if the provider is unknown/its API key isn't set,
    or requests.exceptions.HTTPError if the provider call itself fails.
    Callers decide what to do with either — see get_ai_insight/
    get_ai_training_plan/training_plan_chat in main.py for the three
    different fallback behaviors this used to hand-implement three times.
    """
    provider = provider or get_llm_provider()

    if provider == "local":
        return _call_local_lm_studio(messages, timeout=timeout)

    cfg = PROVIDER_CONFIG.get(provider)
    if not cfg:
        raise LLMConfigError(f"Unknown LLM_PROVIDER '{provider}'. Supported: groq, deepseek, gemini, local.")

    api_key = os.getenv(cfg["key_env"])
    if not api_key:
        raise LLMConfigError(f"{cfg['label']} API key not set in environment ({cfg['key_env']}).")

    payload = {"model": model or cfg["default_model"], "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = requests.post(cfg["url"], json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        body = e.response.text if getattr(e, "response", None) is not None else ""
        print(f"HTTPError on {cfg['label']} API call: {e}. Response body: {body}")
        raise

    data = response.json()
    if "choices" in data and len(data["choices"]) > 0:
        return data["choices"][0]["message"]["content"]
    # An unexpected 200 response shape (e.g. a content-filter/partial-error
    # body with no 'choices') — don't return str(data) as if it were real
    # coaching text; that used to flow straight into the dashboard's
    # (now-fixed) unescaped-innerHTML sink.
    raise LLMConfigError(f"Unexpected response shape from {cfg['label']}: no 'choices' in response.")


def _call_local_lm_studio(messages, timeout=60):
    """LM Studio's proxy expects {'system_prompt', 'input'}, not a standard
    OpenAI messages list, and its response shape varies — kept separate
    from the three hosted providers above rather than forced into the same
    shape."""
    system_prompt = next((m["content"] for m in messages if m.get("role") == "system"), "")
    user_prompt = "\n".join(m["content"] for m in messages if m.get("role") != "system")

    payload = {"model": LOCAL_LM_STUDIO_MODEL, "system_prompt": system_prompt, "input": user_prompt}
    response = requests.post(LOCAL_LM_STUDIO_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if "choices" in data and len(data["choices"]) > 0 and "message" in data["choices"][0]:
        return data["choices"][0]["message"]["content"]
    elif "output" in data and len(data["output"]) > 0 and "content" in data["output"][0]:
        return data["output"][0]["content"]
    elif "message" in data:
        return data["message"]
    elif "response" in data:
        return data["response"]
    raise LLMConfigError(f"Unexpected response shape from local LM Studio: {data}")


def clean_json_response(content: str) -> str:
    """Strips markdown code-fence formatting an LLM sometimes wraps its
    JSON response in, despite being asked not to."""
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    # The fence-stripping above only matches a response that starts with
    # ``` — it didn't cover a model prefacing JSON with prose (e.g. "Here's
    # your plan:\n```json\n{...}\n```"), which previously fell through to
    # json.loads() failing on the whole string. Falls back to extracting
    # the first {...} block via regex; not a full parser (a stray brace in
    # surrounding prose could still confuse it), just a better heuristic
    # than requiring the response to start with a fence.
    if not content.startswith("{"):
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            content = match.group(0)

    return content.strip()


# ---- Lightweight in-process cache for billed LLM calls ----
# main.py's plain `def` routes run in FastAPI's thread pool, so this needs a
# lock even though it's a simple dict. Not distributed/persistent — fine for
# a single-process deployment; a multi-worker/multi-process deployment would
# need a shared store (Redis etc.) instead. Time-based only (no invalidation
# on new activity data), which is the baseline fix
# agent_prompting/improvement_plan.md Section 3 suggests: "/status/{user_id}
# calls get_ai_insight on every request, no memoization ... cache the insight
# per user for a TTL." This also naturally caps spend/rate — repeated calls
# within the TTL window return the cached value instead of a fresh paid API
# call, rather than needing a separate rate-limiter.
_cache_lock = threading.Lock()
_cache_store = {}  # key -> (expires_at_monotonic, value)


def cached_call(key: str, ttl_seconds: int, compute_fn):
    now = time.monotonic()
    with _cache_lock:
        cached = _cache_store.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]

    value = compute_fn()

    with _cache_lock:
        _cache_store[key] = (now + ttl_seconds, value)
    return value
