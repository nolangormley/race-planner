import os
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


def check_groq_chat():
    load_dotenv(REPO_ROOT / ".env.dev")
    groq_api_key = os.getenv("GROQ_API_KEY")

    if not groq_api_key:
        print("No API key")
        return

    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"}
        ]
    }
    headers = {
        "Authorization": f"Bearer {groq_api_key}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        print("Success!", data)
    except Exception as e:
        print("Error:", e)
        if hasattr(e, 'response') and e.response is not None:
            print("Response:", e.response.text)


if __name__ == "__main__":
    check_groq_chat()
