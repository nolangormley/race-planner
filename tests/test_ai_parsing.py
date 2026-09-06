import unittest
import json
import sys
import os

# Add the project root to the python path so we can import src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.api.main import clean_json_response

class TestAIParsing(unittest.TestCase):

    def test_clean_json_response_valid(self):
        content = '{"message": "hello"}'
        self.assertEqual(clean_json_response(content), '{"message": "hello"}')

    def test_clean_json_response_markdown(self):
        content = '```json\n{"message": "hello"}\n```'
        self.assertEqual(clean_json_response(content), '{"message": "hello"}')

    def test_clean_json_response_markdown_no_json(self):
        content = '```\n{"message": "hello"}\n```'
        self.assertEqual(clean_json_response(content), '{"message": "hello"}')

    def test_clean_json_response_extra_whitespace(self):
        content = '   ```json\n{"message": "hello"}\n```   '
        self.assertEqual(clean_json_response(content), '{"message": "hello"}')

    def test_clean_json_response_prose_prefixed(self):
        # A model prefacing JSON with prose instead of starting the response
        # with a fence — the startswith/endswith fence-stripping alone can't
        # handle this; clean_json_response falls back to extracting the
        # first {...} block via regex.
        content = 'Here is your plan:\n```json\n{"message": "hello"}\n```'
        self.assertEqual(clean_json_response(content), '{"message": "hello"}')

    def test_clean_json_response_prose_prefixed_and_suffixed(self):
        content = 'Sure thing!\n{"message": "hello"}\nLet me know if you need changes.'
        self.assertEqual(clean_json_response(content), '{"message": "hello"}')

    def test_deepseek_schema_parsing(self):
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
        
        self.assertIn("message", parsed)
        self.assertIn("plan_updates", parsed)
        self.assertIn("2026-05-15", parsed["plan_updates"])
        self.assertEqual(parsed["plan_updates"]["2026-05-15"]["workout_type"], "Easy Run")

if __name__ == '__main__':
    unittest.main()
