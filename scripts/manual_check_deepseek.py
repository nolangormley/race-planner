import os
import requests
import json
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

def clean_json_response(content: str) -> str:
    """Helper to strip markdown formatting from LLM JSON responses."""
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()

def check_deepseek_chat():
    load_dotenv(REPO_ROOT / ".env.dev")
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
    if not deepseek_api_key:
        print("Error: DEEPSEEK_API_KEY not found in .env.dev")
        return

    print("Testing Deepseek API Chat Integration...")
    
    system_prompt = (
        "You are an expert running coach AI assistant helping to build and modify a training calendar. Athlete: Nolan Gormley, Gender: M, Weight: 80.0 kg.\n"
        "Today's date is 2026-05-12.\n"
        "The current training plan has these entries:\n"
        "  (No entries yet — this is a blank plan.)\n\n"
        "When the user asks you to modify the plan, respond with a JSON object that has:\n"
        "1. 'message': a friendly conversational reply explaining what you've done or recommending changes.\n"
        "2. 'plan_updates': (optional) an object where keys are 'YYYY-MM-DD' dates and values are objects with keys: "
        "'workout_type', 'training_focus', 'approximate_distance', 'description'. "
        "Only include dates you are adding or changing. To remove a day, set 'workout_type' to 'Delete'.\n"
        "3. 'replace_all': (optional) boolean — if true, the full new plan will be in 'full_plan' instead of 'plan_updates'.\n"
        "4. 'full_plan': (optional) same structure as plan_updates but represents the complete new plan.\n"
        "If the user is just chatting (no plan changes needed), only include 'message'.\n"
        "CRITICAL: Always respond with valid JSON only. Do not wrap the JSON in ```json blocks or markdown. Output raw JSON."
    )
    
    messages_payload = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "I want to train for a 10k next month. Can you make a 3 day plan starting tomorrow?"}
    ]

    url_api = "https://api.deepseek.com/v1/chat/completions"
    payload = {
        "model": "deepseek-chat",
        "messages": messages_payload,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {deepseek_api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url_api, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        raw_content = data["choices"][0]["message"]["content"]
        
        print("\n--- RAW CONTENT FROM DEEPSEEK ---")
        print(raw_content)
        print("---------------------------------\n")
        
        content_clean = clean_json_response(raw_content)
        
        print("--- CLEANED CONTENT ---")
        print(content_clean)
        print("-----------------------\n")
        
        parsed = json.loads(content_clean)
        print("✅ SUCCESS: Successfully parsed JSON into dictionary.")
        print("Message:", parsed.get("message"))
        print("Keys present:", parsed.keys())
        
    except requests.exceptions.HTTPError as e:
        print("HTTP Error:", e)
        if hasattr(e, 'response'):
            print(e.response.text)
    except json.JSONDecodeError as e:
        print("❌ JSONDecodeError: Failed to parse the response.")
        print(str(e))
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    check_deepseek_chat()
