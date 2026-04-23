#!/usr/bin/env python3
"""
ads-mcp-connector — MCP server for Meta Ads + Google Ads
Connects Claude Code to live ad platform data via natural language.

Usage: registered in ~/.claude/settings.json — Claude Code manages this process.
Do not run manually.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Load .env from the directory this script lives in
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv not installed; env vars must be set another way

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    print("Error: MCP SDK not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

import meta_ads
import google_ads

sys.path.insert(0, str(Path(__file__).parent))

server = Server("ads-mcp-connector")


# ─── Tool registry ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="check_connection",
            description="Check whether Meta Ads and Google Ads are connected and credentials are valid. Always call this first.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="exchange_meta_token",
            description="Exchange a short-lived Meta access token (valid 1 hour) for a long-lived token (valid 60 days). Call this instead of having the user run a curl command. Requires app_id and app_secret collected earlier in the /ads-connect flow.",
            inputSchema={
                "type": "object",
                "properties": {
                    "app_id": {"type": "string", "description": "Meta App ID from developers.facebook.com"},
                    "app_secret": {"type": "string", "description": "Meta App Secret from App Settings → Basic"},
                    "short_lived_token": {"type": "string", "description": "Short-lived access token from Graph API Explorer (valid 1 hour)"},
                },
                "required": ["app_id", "app_secret", "short_lived_token"],
            },
        ),
        Tool(
            name="write_env_vars",
            description="Save API credentials to the .env file. Used during /ads-connect setup. Only writes allowlisted keys.",
            inputSchema={
                "type": "object",
                "properties": {
                    "vars": {
                        "type": "object",
                        "description": "Key-value pairs to write to .env. Only credential keys are accepted.",
                    }
                },
                "required": ["vars"],
            },
        ),
        # ── Meta Ads ──
        Tool(
            name="meta_get_account_overview",
            description="Get top-level Meta Ads account stats: total spend, reach, impressions, clicks, CTR for a date range. Note: reported dates are in the ad account's timezone, which may differ from the server timezone.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date_range": {
                        "type": "string",
                        "description": "Preset: today, yesterday, last_7d, last_14d, last_30d, last_90d, last_6_months, last_12_months, this_month, last_month. Or custom JSON: '{\"since\":\"2025-01-01\",\"until\":\"2025-03-31\"}'",
                        "default": "last_30d",
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="meta_get_campaigns",
            description="List all Meta Ads campaigns with spend, impressions, clicks, CTR, and CPC.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date_range": {"type": "string", "default": "last_30d"},
                    "status_filter": {
                        "type": "string",
                        "description": "Filter by status: ACTIVE, PAUSED, or ALL",
                        "default": "ACTIVE",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="meta_get_ad_sets",
            description="List Meta Ads ad sets with targeting summary, budget, reach, and frequency.",
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Filter to a specific campaign (optional)"},
                    "date_range": {"type": "string", "default": "last_30d"},
                },
                "required": [],
            },
        ),
        Tool(
            name="meta_get_ads",
            description="List individual Meta ads with spend, CPA, created_time, and performance metrics. Pulls from insights first (reliable spend data) then enriches with ad metadata. Auto-paginates up to 500 ads.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ad_set_id": {"type": "string", "description": "Filter to a specific ad set (optional)"},
                    "date_range": {
                        "type": "string",
                        "description": "Preset: today, yesterday, last_7d, last_14d, last_30d, last_90d, last_6_months, last_12_months, this_month, last_month. Or custom JSON: '{\"since\":\"2025-01-01\",\"until\":\"2025-03-31\"}'",
                        "default": "last_30d",
                    },
                    "status_filter": {
                        "type": "string",
                        "description": "Filter by ad status: ACTIVE, PAUSED, or ALL. Default ALL (diagnostic needs paused ads with historical spend).",
                        "default": "ALL",
                    },
                    "conversion_event": {
                        "type": "string",
                        "description": "Optional. Filter cost_per_action_type to this event (e.g. 'purchase', 'lead'). Matched loosely against action_type substrings. Adds a top-level 'cpa' field per ad with just that event's cost.",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="meta_get_insights",
            description="Get a breakdown report for a specific Meta campaign, ad set, or ad. When object_level is 'ad', rows include ad_id and ad_name. Supports breakdowns by age, gender, placement, device.",
            inputSchema={
                "type": "object",
                "properties": {
                    "object_id": {"type": "string", "description": "Campaign, ad set, or ad ID"},
                    "object_level": {
                        "type": "string",
                        "description": "Level: campaign, adset, or ad",
                        "default": "campaign",
                    },
                    "date_range": {
                        "type": "string",
                        "description": "Preset: today, yesterday, last_7d, last_14d, last_30d, last_90d, last_6_months, last_12_months, this_month, last_month. Or custom JSON: '{\"since\":\"2025-01-01\",\"until\":\"2025-03-31\"}'",
                        "default": "last_30d",
                    },
                    "breakdowns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional breakdowns: age, gender, placement, device_platform",
                    },
                    "conversion_event": {
                        "type": "string",
                        "description": "Optional. Filter cost_per_action_type to this event (e.g. 'purchase', 'lead'). Adds a top-level 'cpa' field per row.",
                    },
                },
                "required": ["object_id"],
            },
        ),
        Tool(
            name="meta_get_monthly_reach",
            description="Get monthly reach, impressions, and spend for the last N months. Returns one data point per calendar month — used for rolling reach / audience saturation analysis.",
            inputSchema={
                "type": "object",
                "properties": {
                    "months": {
                        "type": "integer",
                        "description": "Number of months to look back (default: 13)",
                        "default": 13,
                    }
                },
                "required": [],
            },
        ),
        # ── Google Ads ──
        Tool(
            name="google_get_account_overview",
            description="Get top-level Google Ads account stats: total cost, conversions, ROAS, impression share.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date_range": {"type": "string", "default": "last_30d"}
                },
                "required": [],
            },
        ),
        Tool(
            name="google_get_campaigns",
            description="List all Google Ads campaigns with cost, clicks, conversions, and ROAS.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date_range": {"type": "string", "default": "last_30d"},
                    "status_filter": {
                        "type": "string",
                        "description": "Filter by status: ENABLED, PAUSED, or ALL",
                        "default": "ENABLED",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="google_get_ad_groups",
            description="List Google Ads ad groups with performance metrics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Filter to a specific campaign (optional)"},
                    "date_range": {"type": "string", "default": "last_30d"},
                },
                "required": [],
            },
        ),
        Tool(
            name="google_get_keywords",
            description="List Google Ads keywords with Quality Score, avg CPC, CTR, and conversions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ad_group_id": {"type": "string", "description": "Filter to a specific ad group (optional)"},
                    "date_range": {"type": "string", "default": "last_30d"},
                    "min_impressions": {"type": "integer", "description": "Minimum impressions filter", "default": 0},
                },
                "required": [],
            },
        ),
        Tool(
            name="google_get_search_terms",
            description="List actual search terms triggering your Google Ads. Critical for finding negative keyword opportunities.",
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Filter to a specific campaign (optional)"},
                    "date_range": {"type": "string", "default": "last_30d"},
                    "min_impressions": {"type": "integer", "description": "Minimum impressions filter", "default": 5},
                },
                "required": [],
            },
        ),
    ]


# ─── Tool dispatcher ───────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = _dispatch(name, arguments)
    except Exception as e:
        result = {"error": "UNEXPECTED_ERROR", "message": str(e)}
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


def _dispatch(name: str, args: dict) -> dict:
    # ── Shared ──
    if name == "check_connection":
        meta_status = meta_ads.check_connection()
        google_status = google_ads.check_connection()
        return {"meta": meta_status, "google": google_status}

    if name == "exchange_meta_token":
        return _exchange_meta_token(
            args["app_id"], args["app_secret"], args["short_lived_token"]
        )

    if name == "write_env_vars":
        return _write_env_vars(args.get("vars", {}))

    # ── Meta ──
    if name == "meta_get_account_overview":
        return meta_ads.get_account_overview(args.get("date_range", "last_30d"))

    if name == "meta_get_campaigns":
        return meta_ads.get_campaigns(
            date_range=args.get("date_range", "last_30d"),
            status_filter=args.get("status_filter", "ACTIVE"),
        )

    if name == "meta_get_ad_sets":
        return meta_ads.get_ad_sets(
            campaign_id=args.get("campaign_id"),
            date_range=args.get("date_range", "last_30d"),
        )

    if name == "meta_get_ads":
        return meta_ads.get_ads(
            ad_set_id=args.get("ad_set_id"),
            date_range=args.get("date_range", "last_30d"),
            status_filter=args.get("status_filter", "ALL"),
            conversion_event=args.get("conversion_event"),
        )

    if name == "meta_get_insights":
        return meta_ads.get_insights(
            object_id=args["object_id"],
            object_level=args.get("object_level", "campaign"),
            date_range=args.get("date_range", "last_30d"),
            breakdowns=args.get("breakdowns"),
            conversion_event=args.get("conversion_event"),
        )

    if name == "meta_get_monthly_reach":
        return meta_ads.get_monthly_reach(months=args.get("months", 13))

    # ── Google ──
    if name == "google_get_account_overview":
        return google_ads.get_account_overview(args.get("date_range", "last_30d"))

    if name == "google_get_campaigns":
        return google_ads.get_campaigns(
            date_range=args.get("date_range", "last_30d"),
            status_filter=args.get("status_filter", "ENABLED"),
        )

    if name == "google_get_ad_groups":
        return google_ads.get_ad_groups(
            campaign_id=args.get("campaign_id"),
            date_range=args.get("date_range", "last_30d"),
        )

    if name == "google_get_keywords":
        return google_ads.get_keywords(
            ad_group_id=args.get("ad_group_id"),
            date_range=args.get("date_range", "last_30d"),
            min_impressions=args.get("min_impressions", 0),
        )

    if name == "google_get_search_terms":
        return google_ads.get_search_terms(
            campaign_id=args.get("campaign_id"),
            date_range=args.get("date_range", "last_30d"),
            min_impressions=args.get("min_impressions", 5),
        )

    return {"error": "UNKNOWN_TOOL", "tool": name}


# ─── .env writer ───────────────────────────────────────────────────────────────

ALLOWED_ENV_KEYS = {
    "META_ACCESS_TOKEN",
    "META_AD_ACCOUNT_ID",
    "META_APP_ID",
    "META_APP_SECRET",
    "GOOGLE_DEVELOPER_TOKEN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "GOOGLE_CUSTOMER_ID",
    "GOOGLE_LOGIN_CUSTOMER_ID",
}


def _exchange_meta_token(app_id: str, app_secret: str, short_lived_token: str) -> dict:
    """Exchange a short-lived Meta token for a 60-day long-lived token."""
    url = "https://graph.facebook.com/v19.0/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_lived_token,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
    except Exception as e:
        return {"error": "REQUEST_FAILED", "message": str(e)}

    if "access_token" in data:
        token = data["access_token"]
        masked = f"...{token[-4:]}" if len(token) > 4 else "****"
        return {
            "long_lived_token": token,
            "masked": masked,
            "expires_in_days": 60,
        }

    err = data.get("error", {})
    return {
        "error": "EXCHANGE_FAILED",
        "message": err.get("message", str(data)),
        "code": err.get("code"),
    }


def _write_env_vars(vars_dict: dict) -> dict:
    """Write credential vars to .env. Only allowlisted keys accepted."""
    env_path = Path(__file__).parent / ".env"

    rejected = [k for k in vars_dict if k not in ALLOWED_ENV_KEYS]
    if rejected:
        return {"error": "REJECTED_KEYS", "rejected": rejected, "allowed": list(ALLOWED_ENV_KEYS)}

    clean = {k: v for k, v in vars_dict.items() if k in ALLOWED_ENV_KEYS and v}
    if not clean:
        return {"error": "NO_VALID_VARS", "message": "No valid, non-empty vars provided."}

    # Read existing .env content
    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text().splitlines()

    existing_keys = {}
    for i, line in enumerate(existing_lines):
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            existing_keys[key] = i

    # Update existing keys or append new ones
    for key, value in clean.items():
        if key in existing_keys:
            existing_lines[existing_keys[key]] = f"{key}={value}"
        else:
            existing_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(existing_lines) + "\n")

    # Reload into current process env so server picks up new values immediately
    for key, value in clean.items():
        os.environ[key] = value

    # Return masked confirmation — never echo the full value
    masked = {k: f"...{v[-4:]}" if len(v) > 4 else "****" for k, v in clean.items()}
    return {
        "written": list(clean.keys()),
        "masked_values": masked,
        "file": str(env_path),
    }


# ─── Entry point ───────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
