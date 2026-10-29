import os
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

SYSTEM_PROMPT = """You are an expert running coach AI assistant helping to build and modify a training calendar. Athlete: Nolan, Gender: N/A, Weight: N/A kg.
Today's date is 2026-05-13.
The current training plan has these entries:
  (No entries yet — this is a blank plan.)

When the user asks you to modify the plan, respond with a JSON object that has:
1. 'message': a friendly conversational reply explaining what you've done or recommending changes.
2. 'plan_updates': (optional) an object where keys are 'YYYY-MM-DD' dates and values are objects with keys: 'workout_type', 'training_focus', 'approximate_distance', 'description'. Only include dates you are adding or changing. To remove a day, set 'workout_type' to 'Delete'.
3. 'replace_all': (optional) boolean — if true, the full new plan will be in 'full_plan' instead of 'plan_updates'.
4. 'full_plan': (optional) same structure as plan_updates but represents the complete new plan.
If the user is just chatting (no plan changes needed), only include 'message'.
CRITICAL TOKEN STRATEGY: To fit long plans in your token limit, you MUST be extremely brief. For plans longer than 8 weeks, use only 1-3 words for 'description', keep 'training_focus' to 1 word, and use 'Rest' with no description for rest days.
CRITICAL: Always respond with valid JSON only. Do not wrap the JSON in ```json blocks or markdown. Output raw JSON."""

USER_PROMPT = "Create a training plan for me. I want it to be loosely based off of the boston marathon training plan, but I want long runs to always land on Sundays. Keep my beginning milage around 20 miles a week and grow from there. Start this plan on may 24th, my marathon is on October 18th. make sure to include a race taper."


def check_gemini_chat():
    load_dotenv(REPO_ROOT / ".env")
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        print("Error: GEMINI_API_KEY not found in .env")
        return

    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    headers = {
        "Authorization": f"Bearer {gemini_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT}
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 8192
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=300)
        print(response.status_code)
        data = response.json()
        if "choices" in data:
            print("Finish reason:", data["choices"][0]["finish_reason"])
            print("Usage:", data.get("usage", {}))
            content = data["choices"][0]["message"]["content"]
            print("Content Length:", len(content))
            print("Content Snippet:", content[-500:])
        else:
            print(data)
    except Exception as e:
        print(e)


if __name__ == "__main__":
    check_gemini_chat()
