"""
setup_yahoo_auth.py — One-time OAuth setup to obtain a Yahoo refresh token.

Run this locally ONCE to get your refresh token, then store it in
.streamlit/secrets.toml (or environment variables) for the deployed app.

Usage:
    python setup_yahoo_auth.py

You'll need your Yahoo Developer app credentials:
  https://developer.yahoo.com/apps/  (create an app with Fantasy Sports read scope)

Steps:
  1. Run this script
  2. Open the displayed URL in your browser
  3. Authorize the app
  4. Paste the authorization code back here
  5. Copy the printed refresh_token into your secrets
"""
import os
import sys
import webbrowser
from urllib.parse import urlencode

import requests

AUTH_BASE  = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL  = "https://api.login.yahoo.com/oauth2/get_token"
REDIRECT   = "oob"   # out-of-band for desktop apps


def main():
    print("=" * 60)
    print("  Thumpers GM — Yahoo OAuth Setup")
    print("=" * 60)
    print()

    client_id     = input("Yahoo Client ID:     ").strip()
    client_secret = input("Yahoo Client Secret: ").strip()

    if not client_id or not client_secret:
        print("ERROR: client_id and client_secret are required.")
        sys.exit(1)

    # Step 1 — Authorization URL
    params = urlencode({
        "client_id":     client_id,
        "redirect_uri":  REDIRECT,
        "response_type": "code",
        "language":      "en-us",
    })
    auth_url = f"{AUTH_BASE}?{params}"

    print()
    print("Opening authorization URL in your browser…")
    print("If it doesn't open automatically, visit:")
    print()
    print(f"  {auth_url}")
    print()

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    code = input("Paste the authorization code from Yahoo: ").strip()
    if not code:
        print("ERROR: No code provided.")
        sys.exit(1)

    # Step 2 — Exchange code for tokens
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": REDIRECT,
        },
        auth=(client_id, client_secret),
        timeout=15,
    )

    try:
        r.raise_for_status()
    except Exception as exc:
        print(f"ERROR: Token exchange failed — {exc}")
        print(r.text)
        sys.exit(1)

    tokens = r.json()
    refresh_token = tokens.get("refresh_token")

    print()
    print("=" * 60)
    print("  SUCCESS! Add the following to .streamlit/secrets.toml:")
    print("=" * 60)
    print()
    print(f'YAHOO_CLIENT_ID     = "{client_id}"')
    print(f'YAHOO_CLIENT_SECRET = "{client_secret}"')
    print(f'YAHOO_REFRESH_TOKEN = "{refresh_token}"')
    print()
    print("Also set YAHOO_LEAGUE_KEY (find it in your Yahoo league URL).")
    print()


if __name__ == "__main__":
    main()
