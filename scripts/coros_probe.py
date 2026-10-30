"""Prints the raw fields COROS returns for the last two days, to see what
the unofficial API actually sends before a field gets mapped. Asks for the
password, logs in once, stores nothing.

    python scripts/coros_probe.py you@example.com [us|eu|asia]
"""
import getpass
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.coros import CorosClient, login  # noqa: E402

MAX_LIST_ITEMS = 6


def trimmed(value):
    """The same structure with long lists cut short, so interval arrays do not drown the fields."""
    if isinstance(value, dict):
        return {key: trimmed(item) for key, item in value.items()}
    if isinstance(value, list) and len(value) > MAX_LIST_ITEMS:
        return [trimmed(item) for item in value[:MAX_LIST_ITEMS]] + [f"... {len(value) - MAX_LIST_ITEMS} more"]
    return value


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    email = sys.argv[1]
    region = sys.argv[2] if len(sys.argv) > 2 else "us"
    session = login(email, getpass.getpass("COROS password: "), region)
    client = CorosClient(session["access_token"], session["user_id"], region)
    today = date.today()

    data = client.get("/analyse/dayDetail/query", {"startDay": (today - timedelta(days=1)).strftime("%Y%m%d"), "endDay": today.strftime("%Y%m%d")})
    print("dayDetail entries, oldest first:")
    for item in data.get("dayList") or []:
        print(json.dumps(trimmed(item), indent=2, sort_keys=True))
    other_keys = sorted(key for key in data if key != "dayList")
    if other_keys:
        print("other dayDetail keys:", other_keys)

    analyse = client.get("/analyse/query")
    print("\n/analyse/query keys:", sorted(analyse.keys()))
    latest = (analyse.get("t7dayList") or [{}])[-1]
    print("latest t7dayList entry:", json.dumps(trimmed(latest), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
