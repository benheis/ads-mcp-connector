#!/usr/bin/env python3
"""
auth_check.py — Connection status checker for ads-mcp-connector.

Run standalone to see the status of your credentials:
  python auth_check.py

Used by install.sh after setup and by the /ads-connect skill.
"""

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import meta_ads
import google_ads


def print_status():
    meta = meta_ads.check_connection()
    google = google_ads.check_connection()

    print()
    print("━" * 49)
    print()
    print("  ADS-MCP-CONNECTOR — CONNECTION STATUS")
    print()
    print("━" * 49)
    print()

    # Meta
    print("  META ADS")
    if meta.get("configured") and meta.get("token_test") == "ok":
        print(f"  ✓  Connected")
        print(f"     Account: {meta.get('account_name', 'unknown')}")
        print(f"     ID:      {meta.get('account_id', 'unknown')}")
        print(f"     Currency:{meta.get('currency', 'unknown')}")
    elif meta.get("configured") and meta.get("token_test") == "failed":
        print(f"  ✗  Credentials set but token test failed")
        err = meta.get("error", {})
        if err.get("error") == "META_TOKEN_EXPIRED":
            print(f"     Token expired — run /ads-connect to renew")
        else:
            print(f"     Error: {err.get('message', 'unknown')}")
    else:
        missing = meta.get("missing_vars", [])
        print(f"  ○  Not configured")
        if missing:
            print(f"     Missing: {', '.join(missing)}")
        print(f"     Run /ads-connect to set up Meta Ads")

    print()

    # Google
    print("  GOOGLE ADS")
    if google.get("configured") and google.get("token_test") == "ok":
        print(f"  ✓  Connected")
        print(f"     Account: {google.get('account_name', 'unknown')}")
        print(f"     ID:      {google.get('customer_id', 'unknown')}")
        print(f"     Currency:{google.get('currency', 'unknown')}")
    elif google.get("configured") and google.get("token_test") == "failed":
        print(f"  ✗  Credentials set but token test failed")
        err = google.get("error", {})
        if err.get("error") == "GOOGLE_TOKEN_INVALID":
            print(f"     Credentials invalid — run /ads-connect to reconfigure")
        else:
            print(f"     Error: {err.get('message', str(err))[:80]}")
    else:
        missing = google.get("missing_vars", [])
        print(f"  ○  Not configured")
        if missing:
            print(f"     Missing: {', '.join(missing)}")
        print(f"     Run /ads-connect to set up Google Ads")

    print()
    print("─" * 49)

    meta_ok = meta.get("configured") and meta.get("token_test") == "ok"
    google_ok = google.get("configured") and google.get("token_test") == "ok"

    if meta_ok and google_ok:
        print()
        print("  Both platforms connected. Open Claude Code")
        print("  and ask about your campaigns.")
    elif meta_ok or google_ok:
        connected = "Meta Ads" if meta_ok else "Google Ads"
        missing_p = "Google Ads" if meta_ok else "Meta Ads"
        print()
        print(f"  {connected} is connected.")
        print(f"  Run /ads-connect to add {missing_p}.")
    else:
        print()
        print("  No platforms connected yet.")
        print("  Open Claude Code and type /ads-connect")

    print()


if __name__ == "__main__":
    print_status()
