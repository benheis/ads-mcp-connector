#!/usr/bin/env python3
"""
get_google_token.py — Generate a Google Ads OAuth2 refresh token.

This script opens your browser, walks you through Google's login screen,
and prints a refresh token you can use with ads-mcp-connector.

Usage:
  python get_google_token.py

You'll need your Google Cloud OAuth2 credentials first.
The /ads-connect skill will tell you when and how to run this.
"""

import sys
import os

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print()
    print("  Missing dependency. Run:")
    print("  venv/bin/pip install google-auth-oauthlib")
    print()
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/adwords"]


def main():
    print()
    print("━" * 49)
    print()
    print("  GOOGLE ADS — GET REFRESH TOKEN")
    print()
    print("━" * 49)
    print()
    print("  You need your OAuth2 Client ID and Secret.")
    print("  Get them from Google Cloud Console:")
    print("  console.cloud.google.com → APIs & Services")
    print("  → Credentials → your OAuth 2.0 Client ID")
    print()

    client_id = input("  Paste your Client ID and press Enter:\n  > ").strip()
    if not client_id:
        print("  No Client ID entered. Exiting.")
        sys.exit(1)

    client_secret = input("\n  Paste your Client Secret and press Enter:\n  > ").strip()
    if not client_secret:
        print("  No Client Secret entered. Exiting.")
        sys.exit(1)

    print()
    print("  Opening your browser for Google login...")
    print("  If it doesn't open automatically, copy the URL")
    print("  from the terminal and paste it into your browser.")
    print()

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    try:
        flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
        credentials = flow.run_local_server(port=8080, prompt="consent")
    except Exception as e:
        print(f"  Error during authentication: {e}")
        print()
        print("  Common issues:")
        print("  - Wrong Client ID or Secret (double-check for spaces)")
        print("  - OAuth consent screen not configured in Google Cloud")
        print("  - Port 8080 blocked — try closing other apps")
        sys.exit(1)

    refresh_token = credentials.refresh_token
    if not refresh_token:
        print()
        print("  No refresh token returned.")
        print("  This can happen if you've already authorized this app.")
        print("  Go to myaccount.google.com/permissions, revoke access")
        print("  for your app, then run this script again.")
        sys.exit(1)

    print()
    print("━" * 49)
    print()
    print("  SUCCESS — Your refresh token:")
    print()
    print(f"  {refresh_token}")
    print()
    print("━" * 49)
    print()
    print("  Copy the token above and paste it back into")
    print("  Claude Code when prompted by /ads-connect.")
    print()
    print("  This token is permanent — you won't need to")
    print("  run this script again unless you revoke access.")
    print()


if __name__ == "__main__":
    main()
