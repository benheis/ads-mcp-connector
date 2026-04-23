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

# Inject system trust store so requests work behind corporate TLS proxies
# (Zscaler, Netskope, Cloudflare WARP, etc.) without manual cert configuration.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

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
            description="Get a breakdown report for a specific Meta campaign, ad set, or ad. When object_level is 'ad', rows include ad_id and ad_name. Supports breakdowns by age, gender, placement, device. Set time_increment for per-month or per-day splits.",
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
                    "time_increment": {
                        "type": "string",
                        "description": "Optional time bucketing: 'monthly', 'weekly', or '1' (daily). When set, each row has date_start/date_stop instead of a single total.",
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
        Tool(
            name="meta_get_ad_monthly_spend",
            description="Get per-ad spend broken down by calendar month. Returns one entry per ad with a monthly_spend array — the data needed for creative cohort / churn analysis.",
            inputSchema={
                "type": "object",
                "properties": {
                    "months": {
                        "type": "integer",
                        "description": "Number of months to look back (default: 6)",
                        "default": 6,
                    },
                    "status_filter": {
                        "type": "string",
                        "description": "Filter by ad status: ALL, ACTIVE, PAUSED, ARCHIVED (default: ALL)",
                        "default": "ALL",
                    },
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
        # ── Meta Writes ──
        Tool(
            name="meta_update_campaign_status",
            description="Pause or enable a Meta Ads campaign.",
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Meta campaign ID"},
                    "status": {"type": "string", "description": "ACTIVE or PAUSED"},
                },
                "required": ["campaign_id", "status"],
            },
        ),
        Tool(
            name="meta_update_ad_set_status",
            description="Pause or enable a Meta Ads ad set.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ad_set_id": {"type": "string", "description": "Meta ad set ID"},
                    "status": {"type": "string", "description": "ACTIVE or PAUSED"},
                },
                "required": ["ad_set_id", "status"],
            },
        ),
        Tool(
            name="meta_update_ad_status",
            description="Pause or enable an individual Meta ad.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ad_id": {"type": "string", "description": "Meta ad ID"},
                    "status": {"type": "string", "description": "ACTIVE or PAUSED"},
                },
                "required": ["ad_id", "status"],
            },
        ),
        Tool(
            name="meta_update_budget",
            description="Update budget on a Meta campaign (CBO) or ad set. For CBO ad sets, also sets per-ad-set daily min/max spend constraints.",
            inputSchema={
                "type": "object",
                "properties": {
                    "object_id": {"type": "string", "description": "Campaign ID or ad set ID"},
                    "object_type": {"type": "string", "description": "campaign or ad_set"},
                    "budget_type": {"type": "string", "description": "daily or lifetime"},
                    "amount_dollars": {"type": "number", "description": "Budget amount in dollars"},
                    "daily_min_dollars": {"type": "number", "description": "CBO ad set daily minimum spend (optional, ad_set only)"},
                    "daily_max_dollars": {"type": "number", "description": "CBO ad set daily maximum spend cap (optional, ad_set only)"},
                },
                "required": ["object_id", "object_type", "budget_type", "amount_dollars"],
            },
        ),
        Tool(
            name="meta_create_campaign",
            description="Create a new Meta Ads campaign. Defaults to PAUSED — always review before activating.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "objective": {"type": "string", "description": "OUTCOME_TRAFFIC, OUTCOME_LEADS, OUTCOME_SALES, OUTCOME_AWARENESS, OUTCOME_ENGAGEMENT, OUTCOME_APP_PROMOTION"},
                    "budget_type": {"type": "string", "description": "daily or lifetime"},
                    "amount_dollars": {"type": "number", "description": "Budget amount in dollars"},
                    "status": {"type": "string", "default": "PAUSED", "description": "ACTIVE or PAUSED"},
                    "special_ad_categories": {"type": "array", "items": {"type": "string"}, "description": "Required for housing, employment, credit ads. Pass [] if none."},
                },
                "required": ["name", "objective", "budget_type", "amount_dollars"],
            },
        ),
        Tool(
            name="meta_create_ad_set",
            description="Create a new Meta Ads ad set inside a campaign. Defaults to PAUSED.",
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string"},
                    "name": {"type": "string"},
                    "optimization_goal": {"type": "string", "description": "OFFSITE_CONVERSIONS, LINK_CLICKS, REACH, IMPRESSIONS, LANDING_PAGE_VIEWS, LEAD_GENERATION"},
                    "billing_event": {"type": "string", "default": "IMPRESSIONS"},
                    "bid_strategy": {"type": "string", "default": "LOWEST_COST_WITHOUT_CAP", "description": "LOWEST_COST_WITHOUT_CAP, LOWEST_COST_WITH_BID_CAP, COST_CAP"},
                    "daily_budget_dollars": {"type": "number", "description": "Daily budget in dollars (use this or lifetime_budget_dollars)"},
                    "lifetime_budget_dollars": {"type": "number", "description": "Lifetime budget in dollars"},
                    "targeting": {"type": "object", "description": "Meta targeting spec dict (geo, age, interests, etc.)"},
                    "start_time": {"type": "string", "description": "ISO 8601 start time e.g. '2025-05-01T00:00:00-0500'"},
                    "end_time": {"type": "string", "description": "ISO 8601 end time (optional)"},
                    "status": {"type": "string", "default": "PAUSED"},
                },
                "required": ["campaign_id", "name", "optimization_goal"],
            },
        ),
        Tool(
            name="meta_create_ad",
            description="Create a Meta ad linking to an existing ad creative. Defaults to PAUSED — review creative before activating.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ad_set_id": {"type": "string"},
                    "name": {"type": "string"},
                    "creative_id": {"type": "string", "description": "ID from meta_create_ad_creative"},
                    "status": {"type": "string", "default": "PAUSED"},
                },
                "required": ["ad_set_id", "name", "creative_id"],
            },
        ),
        Tool(
            name="meta_upload_image",
            description="Upload a static image to the Meta ad account image library. Returns image_hash for use in meta_create_ad_creative.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to JPG or PNG file on the local machine"},
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="meta_upload_video",
            description="Upload a video to the Meta ad account video library. Returns video_id for use in meta_create_ad_creative. Encoding is async — video may take a few minutes to become available.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to MP4 or MOV file on the local machine"},
                    "title": {"type": "string", "description": "Optional title for the video in the library"},
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="meta_create_ad_creative",
            description="Create a Meta ad creative using an uploaded image or video. Returns creative_id for use in meta_create_ad.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Internal name for the creative"},
                    "page_id": {"type": "string", "description": "Facebook Page ID associated with the ad account"},
                    "link_url": {"type": "string", "description": "Destination URL for the ad"},
                    "message": {"type": "string", "description": "Primary ad copy (body text)"},
                    "headline": {"type": "string", "description": "Ad headline"},
                    "description": {"type": "string", "description": "Ad description (optional)"},
                    "call_to_action_type": {"type": "string", "default": "LEARN_MORE", "description": "LEARN_MORE, SHOP_NOW, SIGN_UP, GET_QUOTE, DOWNLOAD, CONTACT_US, APPLY_NOW, GET_OFFER"},
                    "image_hash": {"type": "string", "description": "Image hash from meta_upload_image (use this or video_id)"},
                    "video_id": {"type": "string", "description": "Video ID from meta_upload_video (use this or image_hash)"},
                },
                "required": ["name", "page_id", "link_url", "message", "headline"],
            },
        ),
        Tool(
            name="meta_get_ad_images",
            description="List all images in the Meta ad account image library with their hashes, URLs, and dimensions.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        # ── Google Writes ──
        Tool(
            name="google_list_negative_keywords",
            description="List existing negative keywords at campaign or ad group level. Returns criterion IDs needed for removal.",
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Filter to a specific campaign (optional)"},
                    "ad_group_id": {"type": "string", "description": "Filter to a specific ad group — also returns ad group negatives when set"},
                },
                "required": [],
            },
        ),
        Tool(
            name="google_add_negative_keywords",
            description="Add negative keywords to a Google Ads campaign or ad group.",
            inputSchema={
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}, "description": "List of keyword strings to add as negatives"},
                    "match_type": {"type": "string", "description": "EXACT, PHRASE, or BROAD"},
                    "level": {"type": "string", "description": "campaign or ad_group"},
                    "campaign_id": {"type": "string", "description": "Required for both levels"},
                    "ad_group_id": {"type": "string", "description": "Required when level is ad_group"},
                },
                "required": ["keywords", "match_type", "level", "campaign_id"],
            },
        ),
        Tool(
            name="google_remove_negative_keywords",
            description="Remove negative keywords by criterion ID. Get criterion IDs from google_list_negative_keywords first.",
            inputSchema={
                "type": "object",
                "properties": {
                    "criterion_ids": {"type": "array", "items": {"type": "string"}, "description": "List of criterion_id values to remove"},
                    "level": {"type": "string", "description": "campaign or ad_group"},
                    "campaign_id": {"type": "string", "description": "Required for both levels"},
                    "ad_group_id": {"type": "string", "description": "Required when level is ad_group"},
                },
                "required": ["criterion_ids", "level", "campaign_id"],
            },
        ),
        Tool(
            name="google_update_campaign_status",
            description="Pause or enable a Google Ads campaign.",
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string"},
                    "status": {"type": "string", "description": "ENABLED or PAUSED"},
                },
                "required": ["campaign_id", "status"],
            },
        ),
        Tool(
            name="google_update_ad_group_status",
            description="Pause or enable a Google Ads ad group.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ad_group_id": {"type": "string"},
                    "status": {"type": "string", "description": "ENABLED or PAUSED"},
                },
                "required": ["ad_group_id", "status"],
            },
        ),
        Tool(
            name="google_update_keyword_bid",
            description="Update the max CPC bid for a positive keyword. Get criterion_id from google_get_keywords.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ad_group_id": {"type": "string"},
                    "criterion_id": {"type": "string", "description": "Keyword criterion ID from google_get_keywords"},
                    "bid_dollars": {"type": "number", "description": "New max CPC in account currency (e.g. 1.50)"},
                },
                "required": ["ad_group_id", "criterion_id", "bid_dollars"],
            },
        ),
        Tool(
            name="google_update_campaign_budget",
            description="Update the daily budget for a Google Ads campaign.",
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string"},
                    "daily_budget_dollars": {"type": "number", "description": "New daily budget in account currency"},
                },
                "required": ["campaign_id", "daily_budget_dollars"],
            },
        ),
        Tool(
            name="google_create_campaign",
            description="Create a new Google Ads campaign with a budget. Defaults to PAUSED — always review before activating.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "channel_type": {"type": "string", "description": "SEARCH, DISPLAY, or PERFORMANCE_MAX"},
                    "bidding_strategy": {"type": "string", "description": "MAXIMIZE_CONVERSIONS, TARGET_CPA, MANUAL_CPC, MAXIMIZE_CONVERSION_VALUE"},
                    "daily_budget_dollars": {"type": "number"},
                    "status": {"type": "string", "default": "PAUSED"},
                    "target_cpa_dollars": {"type": "number", "description": "Required when bidding_strategy is TARGET_CPA"},
                },
                "required": ["name", "channel_type", "bidding_strategy", "daily_budget_dollars"],
            },
        ),
        Tool(
            name="google_create_ad_group",
            description="Create an ad group inside an existing Google Ads campaign.",
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string"},
                    "name": {"type": "string"},
                    "cpc_bid_dollars": {"type": "number", "description": "Default max CPC for keywords in this group", "default": 1.0},
                    "status": {"type": "string", "default": "ENABLED"},
                },
                "required": ["campaign_id", "name"],
            },
        ),
        Tool(
            name="google_create_responsive_search_ad",
            description="Create a Responsive Search Ad (RSA) in an ad group. The ad is created PAUSED — review before enabling.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ad_group_id": {"type": "string"},
                    "headlines": {"type": "array", "items": {"type": "string"}, "description": "3–15 headline strings, max 30 chars each"},
                    "descriptions": {"type": "array", "items": {"type": "string"}, "description": "2–4 description strings, max 90 chars each"},
                    "final_url": {"type": "string", "description": "Landing page URL"},
                    "path1": {"type": "string", "description": "Display URL path 1 (optional, max 15 chars)"},
                    "path2": {"type": "string", "description": "Display URL path 2 (optional, max 15 chars)"},
                },
                "required": ["ad_group_id", "headlines", "descriptions", "final_url"],
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
            time_increment=args.get("time_increment"),
        )

    if name == "meta_get_monthly_reach":
        return meta_ads.get_monthly_reach(months=args.get("months", 13))

    if name == "meta_get_ad_monthly_spend":
        return meta_ads.get_ad_monthly_spend(
            months=args.get("months", 6),
            status_filter=args.get("status_filter", "ALL"),
        )

    # ── Meta Writes ──
    if name == "meta_update_campaign_status":
        return meta_ads.update_campaign_status(args["campaign_id"], args["status"])

    if name == "meta_update_ad_set_status":
        return meta_ads.update_ad_set_status(args["ad_set_id"], args["status"])

    if name == "meta_update_ad_status":
        return meta_ads.update_ad_status(args["ad_id"], args["status"])

    if name == "meta_update_budget":
        return meta_ads.update_budget(
            object_id=args["object_id"],
            object_type=args["object_type"],
            budget_type=args["budget_type"],
            amount_dollars=args["amount_dollars"],
            daily_min_dollars=args.get("daily_min_dollars"),
            daily_max_dollars=args.get("daily_max_dollars"),
        )

    if name == "meta_create_campaign":
        return meta_ads.create_campaign(
            name=args["name"],
            objective=args["objective"],
            budget_type=args["budget_type"],
            amount_dollars=args["amount_dollars"],
            status=args.get("status", "PAUSED"),
            special_ad_categories=args.get("special_ad_categories", []),
        )

    if name == "meta_create_ad_set":
        return meta_ads.create_ad_set(
            campaign_id=args["campaign_id"],
            name=args["name"],
            optimization_goal=args["optimization_goal"],
            billing_event=args.get("billing_event", "IMPRESSIONS"),
            bid_strategy=args.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
            daily_budget_dollars=args.get("daily_budget_dollars"),
            lifetime_budget_dollars=args.get("lifetime_budget_dollars"),
            targeting=args.get("targeting"),
            start_time=args.get("start_time"),
            end_time=args.get("end_time"),
            status=args.get("status", "PAUSED"),
        )

    if name == "meta_create_ad":
        return meta_ads.create_ad(
            ad_set_id=args["ad_set_id"],
            name=args["name"],
            creative_id=args["creative_id"],
            status=args.get("status", "PAUSED"),
        )

    if name == "meta_upload_image":
        return meta_ads.upload_image(file_path=args["file_path"])

    if name == "meta_upload_video":
        return meta_ads.upload_video(
            file_path=args["file_path"],
            title=args.get("title"),
        )

    if name == "meta_create_ad_creative":
        return meta_ads.create_ad_creative(
            name=args["name"],
            page_id=args["page_id"],
            link_url=args["link_url"],
            message=args["message"],
            headline=args["headline"],
            description=args.get("description", ""),
            call_to_action_type=args.get("call_to_action_type", "LEARN_MORE"),
            image_hash=args.get("image_hash"),
            video_id=args.get("video_id"),
        )

    if name == "meta_get_ad_images":
        return meta_ads.get_ad_images()

    # ── Google Writes ──
    if name == "google_list_negative_keywords":
        return google_ads.list_negative_keywords(
            campaign_id=args.get("campaign_id"),
            ad_group_id=args.get("ad_group_id"),
        )

    if name == "google_add_negative_keywords":
        return google_ads.add_negative_keywords(
            keywords=args["keywords"],
            match_type=args["match_type"],
            level=args["level"],
            campaign_id=args["campaign_id"],
            ad_group_id=args.get("ad_group_id"),
        )

    if name == "google_remove_negative_keywords":
        return google_ads.remove_negative_keywords(
            criterion_ids=args["criterion_ids"],
            level=args["level"],
            campaign_id=args["campaign_id"],
            ad_group_id=args.get("ad_group_id"),
        )

    if name == "google_update_campaign_status":
        return google_ads.update_campaign_status(args["campaign_id"], args["status"])

    if name == "google_update_ad_group_status":
        return google_ads.update_ad_group_status(args["ad_group_id"], args["status"])

    if name == "google_update_keyword_bid":
        return google_ads.update_keyword_bid(
            ad_group_id=args["ad_group_id"],
            criterion_id=args["criterion_id"],
            bid_dollars=args["bid_dollars"],
        )

    if name == "google_update_campaign_budget":
        return google_ads.update_campaign_budget(
            campaign_id=args["campaign_id"],
            daily_budget_dollars=args["daily_budget_dollars"],
        )

    if name == "google_create_campaign":
        return google_ads.create_campaign(
            name=args["name"],
            channel_type=args["channel_type"],
            bidding_strategy=args["bidding_strategy"],
            daily_budget_dollars=args["daily_budget_dollars"],
            status=args.get("status", "PAUSED"),
            target_cpa_dollars=args.get("target_cpa_dollars"),
        )

    if name == "google_create_ad_group":
        return google_ads.create_ad_group(
            campaign_id=args["campaign_id"],
            name=args["name"],
            cpc_bid_dollars=args.get("cpc_bid_dollars", 1.0),
            status=args.get("status", "ENABLED"),
        )

    if name == "google_create_responsive_search_ad":
        return google_ads.create_responsive_search_ad(
            ad_group_id=args["ad_group_id"],
            headlines=args["headlines"],
            descriptions=args["descriptions"],
            final_url=args["final_url"],
            path1=args.get("path1", ""),
            path2=args.get("path2", ""),
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
