"""Manage the Strava push subscription for this app.

    python scripts/strava_webhook.py view
    python scripts/strava_webhook.py create https://yasmodeus.andrew.cmu.edu/api/strava/webhook
    python scripts/strava_webhook.py delete <subscription id>

Reads STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET and STRAVA_WEBHOOK_VERIFY_TOKEN
from the environment (or .env). Strava validates the callback URL during
`create`, so the app must already be running there with the same verify
token. One subscription per Strava application.
"""
import os
import sys

import requests
from dotenv import load_dotenv

API = "https://www.strava.com/api/v3/push_subscriptions"


def main(argv):
    load_dotenv()
    client_id = os.environ["STRAVA_CLIENT_ID"]
    client_secret = os.environ["STRAVA_CLIENT_SECRET"]
    auth = {"client_id": client_id, "client_secret": client_secret}
    command = argv[1] if len(argv) > 1 else "view"

    if command == "view":
        response = requests.get(API, params=auth, timeout=30)
    elif command == "create":
        verify_token = os.environ.get("STRAVA_WEBHOOK_VERIFY_TOKEN")
        if not verify_token:
            sys.exit("Set STRAVA_WEBHOOK_VERIFY_TOKEN first (the app must run with the same value).")
        response = requests.post(API, data={**auth, "callback_url": argv[2], "verify_token": verify_token}, timeout=30)
    elif command == "delete":
        response = requests.delete(f"{API}/{argv[2]}", params=auth, timeout=30)
    else:
        sys.exit(__doc__)

    print(response.status_code, response.text or "(no body)")
    response.raise_for_status()


if __name__ == "__main__":
    main(sys.argv)
