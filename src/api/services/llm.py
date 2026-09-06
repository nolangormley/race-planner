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
import requests

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
    return content.strip()
