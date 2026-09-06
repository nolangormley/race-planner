import pytest
from src.api.main import clean_json_response
import json

def test_clean_json_response_valid():
    content = '{"message": "hello"}'
    assert clean_json_response(content) == '{"message": "hello"}'

def test_clean_json_response_markdown():
    content = '```json\n{"message": "hello"}\n```'
    assert clean_json_response(content) == '{"message": "hello"}'

def test_clean_json_response_markdown_no_json():
    content = '```\n{"message": "hello"}\n```'
    assert clean_json_response(content) == '{"message": "hello"}'

def test_clean_json_response_extra_whitespace():
    content = '   ```json\n{"message": "hello"}\n```   '
    assert clean_json_response(content) == '{"message": "hello"}'

def test_clean_json_response_prose_prefixed():
    # A model prefacing JSON with prose instead of starting the response
    # with a fence — the startswith/endswith fence-stripping alone can't
    # handle this; clean_json_response falls back to extracting the first
    # {...} block via regex.
    content = 'Here is your plan:\n```json\n{"message": "hello"}\n```'
    assert clean_json_response(content) == '{"message": "hello"}'

def test_clean_json_response_prose_prefixed_and_suffixed():
    content = 'Sure thing!\n{"message": "hello"}\nLet me know if you need changes.'
    assert clean_json_response(content) == '{"message": "hello"}'

def test_deepseek_schema_parsing():
    # Simulate a realistic deepseek response where it sometimes messes up
    # We will test the actual parsing logic used in main.py
    
    mock_response = '''```json
{
  "message": "I updated your plan.",
  "plan_updates": {
    "2026-05-15": {
      "workout_type": "Easy Run",
      "training_focus": "Base",
      "approximate_distance": "5k",
      "description": "Nice easy run"
    }
  }
}
```'''
    cleaned = clean_json_response(mock_response)
    parsed = json.loads(cleaned)
    assert "message" in parsed
    assert "plan_updates" in parsed
    assert "2026-05-15" in parsed["plan_updates"]
    assert parsed["plan_updates"]["2026-05-15"]["workout_type"] == "Easy Run"
