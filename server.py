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
            description="Get top-level Meta Ads account stats: total spend, reach, impressions, clicks, CTR for a date range.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date_range": {
                        "type": "string",
                        "description": "Date range. Options: today, yesterday, last_7d, last_14d, last_30d, last_90d, this_month, last_month",
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
            description="List individual Meta ads with creative info and performance metrics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ad_set_id": {"type": "string", "description": "Filter to a specific ad set (optional)"},
                    "date_range": {"type": "string", "default": "last_30d"},
                },
                "required": [],
            },
        ),
        Tool(
            name="meta_get_insights",
            description="Get a breakdown report for a specific Meta campaign, ad set, or ad. Supports breakdowns by age, gender, placement, device.",
            inputSchema={
                "type": "object",
                "properties": {
                    "object_id": {"type": "string", "description": "Campaign, ad set, or ad ID"},
                    "object_level": {
                        "type": "string",
                        "description": "Level: campaign, adset, or ad",
                        "default": "campaign",
                    },
                    "date_range": {"type": "string", "default": "last_30d"},
                    "breakdowns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional breakdowns: age, gender, placement, device_platform",
                    },
                },
                "required": ["object_id"],
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
        )

    if name == "meta_get_insights":
        return meta_ads.get_insights(
            object_id=args["object_id"],
            object_level=args.get("object_level", "campaign"),
            date_range=args.get("date_range", "last_30d"),
            breakdowns=args.get("breakdowns"),
        )

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
    "GOOGLE_DEVELOPER_TOKEN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "GOOGLE_CUSTOMER_ID",
    "GOOGLE_LOGIN_CUSTOMER_ID",
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
