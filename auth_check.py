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


def _next_step_message(platform: str) -> tuple[str, str]:
    """Return (single-line hint, connect command) based on platform choice."""
    if platform == "1":   # Claude Code
        return ("Open a terminal, type: claude", "then type: /ads-connect")
    elif platform == "2": # Claude Desktop / Cowork
        return ("Quit and reopen Claude Desktop,", "then ask: Connect my Meta Ads account")
    elif platform == "3": # Cursor
        return ("Quit and reopen Cursor, open Agent mode (Cmd+I),", "then ask: Connect my Meta Ads account")
    else:
        return ("Open your AI tool and", "ask it to connect your ad accounts")


def print_status(platform: str = ""):
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

    hint, cmd = _next_step_message(platform)
    if meta_ok and google_ok:
        print()
        print("  Both platforms connected.")
        print(f"  {hint}")
        print(f"  {cmd}")
    elif meta_ok or google_ok:
        connected = "Meta Ads" if meta_ok else "Google Ads"
        missing_p = "Google Ads" if meta_ok else "Meta Ads"
        print()
        print(f"  {connected} is connected.")
        print(f"  {missing_p} is not connected yet.")
        print(f"  {hint}")
        print(f"  {cmd}")
    else:
        print()
        print("  No platforms connected yet.")
        print(f"  {hint}")
        print(f"  {cmd}")

    print()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", default="", help="1=Claude Code, 2=Claude Desktop, 3=Cursor")
    args = parser.parse_args()
    print_status(platform=args.platform)
