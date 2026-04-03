#!/usr/bin/env python3
"""
Meta Ads API client for ads-mcp-connector.
Uses Meta Graph API v19.0 via requests.
Credentials loaded from environment (META_ACCESS_TOKEN, META_AD_ACCOUNT_ID).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

NOT_CONFIGURED = {
    "error": "META_NOT_CONFIGURED",
    "hint": "Run /ads-connect in Claude Code to set up Meta Ads.",
    "missing": []
}


def _check_config() -> dict | None:
    """Return error dict if credentials are missing, None if all good."""
    missing = []
    if not os.environ.get("META_ACCESS_TOKEN"):
        missing.append("META_ACCESS_TOKEN")
    if not os.environ.get("META_AD_ACCOUNT_ID"):
        missing.append("META_AD_ACCOUNT_ID")
    if not HAS_REQUESTS:
        return {"error": "MISSING_DEPENDENCY", "hint": "Run: pip install requests"}
    if missing:
        err = dict(NOT_CONFIGURED)
        err["missing"] = missing
        return err
    return None


def _token() -> str:
    return os.environ.get("META_ACCESS_TOKEN", "")


def _account_id() -> str:
    """Return account ID, ensuring it has the act_ prefix."""
    raw = os.environ.get("META_AD_ACCOUNT_ID", "")
    if raw and not raw.startswith("act_"):
        return f"act_{raw}"
    return raw


def _date_range_params(date_range: str) -> dict:
    """Convert a friendly date range string to Meta API time_range params."""
    today = datetime.today()
    ranges = {
        "today": (today, today),
        "yesterday": (today - timedelta(1), today - timedelta(1)),
        "last_7d": (today - timedelta(7), today),
        "last_14d": (today - timedelta(14), today),
        "last_30d": (today - timedelta(30), today),
        "last_90d": (today - timedelta(90), today),
        "this_month": (today.replace(day=1), today),
        "last_month": (
            (today.replace(day=1) - timedelta(1)).replace(day=1),
            today.replace(day=1) - timedelta(1),
        ),
    }
    if date_range not in ranges:
        # Default to last 30 days for unrecognized values
        date_range = "last_30d"
    since, until = ranges[date_range]
    return {
        "since": since.strftime("%Y-%m-%d"),
        "until": until.strftime("%Y-%m-%d"),
    }


def _get(endpoint: str, params: dict) -> dict:
    """Make a Graph API GET request. Returns parsed JSON or error dict."""
    params["access_token"] = _token()
    try:
        resp = requests.get(f"{GRAPH_API_BASE}/{endpoint}", params=params, timeout=30)
        data = resp.json()
        if "error" in data:
            code = data["error"].get("code")
            msg = data["error"].get("message", "Unknown Meta API error")
            if code == 190:
                return {
                    "error": "META_TOKEN_EXPIRED",
                    "message": msg,
                    "hint": "Your Meta token has expired. Run /ads-connect to renew it.",
                }
            return {"error": "META_API_ERROR", "code": code, "message": msg}
        return data
    except requests.exceptions.Timeout:
        return {"error": "TIMEOUT", "hint": "Meta API request timed out. Try again."}
    except Exception as e:
        return {"error": "REQUEST_FAILED", "message": str(e)}


# ─── Tool implementations ──────────────────────────────────────────────────────


def get_account_overview(date_range: str = "last_30d") -> dict:
    """Top-level account stats: spend, reach, impressions, clicks, CTR."""
    err = _check_config()
    if err:
        return err

    time_range = _date_range_params(date_range)
    fields = "spend,reach,impressions,clicks,ctr,cpc,cpm,actions"
    data = _get(
        f"{_account_id()}/insights",
        {
            "fields": fields,
            "time_range": json.dumps(time_range),
            "level": "account",
        },
    )
    if "error" in data:
        return data

    insights = data.get("data", [{}])
    result = insights[0] if insights else {}
    result["date_range"] = date_range
    result["account_id"] = _account_id()
    return result


def get_campaigns(date_range: str = "last_30d", status_filter: str = "ACTIVE") -> dict:
    """All campaigns with spend, impressions, clicks, CTR, CPC."""
    err = _check_config()
    if err:
        return err

    time_range = _date_range_params(date_range)

    # Get campaign list
    filtering = []
    if status_filter and status_filter != "ALL":
        filtering = [{"field": "effective_status", "operator": "IN", "value": [status_filter]}]

    campaigns_data = _get(
        f"{_account_id()}/campaigns",
        {
            "fields": "id,name,status,objective,daily_budget,lifetime_budget",
            "filtering": json.dumps(filtering) if filtering else "[]",
            "limit": 50,
        },
    )
    if "error" in campaigns_data:
        return campaigns_data

    campaigns = campaigns_data.get("data", [])
    if not campaigns:
        return {"campaigns": [], "date_range": date_range, "count": 0}

    # Get insights for all campaigns in one batch
    campaign_ids = [c["id"] for c in campaigns]
    insights_data = _get(
        f"{_account_id()}/insights",
        {
            "fields": "campaign_id,campaign_name,spend,impressions,clicks,ctr,cpc,cpm,reach",
            "time_range": json.dumps(time_range),
            "level": "campaign",
            "limit": 50,
        },
    )

    # Merge insights into campaign list
    insights_by_id = {}
    if "data" in insights_data:
        for row in insights_data["data"]:
            insights_by_id[row.get("campaign_id")] = row

    result_campaigns = []
    for c in campaigns:
        ins = insights_by_id.get(c["id"], {})
        result_campaigns.append({
            "id": c["id"],
            "name": c["name"],
            "status": c["status"],
            "objective": c.get("objective"),
            "spend": ins.get("spend", "0"),
            "impressions": ins.get("impressions", "0"),
            "clicks": ins.get("clicks", "0"),
            "ctr": ins.get("ctr", "0"),
            "cpc": ins.get("cpc", "0"),
            "reach": ins.get("reach", "0"),
        })

    # Sort by spend descending
    result_campaigns.sort(key=lambda x: float(x.get("spend", 0) or 0), reverse=True)

    return {
        "campaigns": result_campaigns,
        "date_range": date_range,
        "count": len(result_campaigns),
    }


def get_ad_sets(campaign_id: str = None, date_range: str = "last_30d") -> dict:
    """Ad sets with targeting summary, budget, and delivery status."""
    err = _check_config()
    if err:
        return err

    time_range = _date_range_params(date_range)
    filtering = []
    if campaign_id:
        filtering = [{"field": "campaign_id", "operator": "EQUAL", "value": campaign_id}]

    data = _get(
        f"{_account_id()}/adsets",
        {
            "fields": "id,name,status,campaign_id,daily_budget,lifetime_budget,targeting,billing_event,optimization_goal",
            "filtering": json.dumps(filtering) if filtering else "[]",
            "limit": 50,
        },
    )
    if "error" in data:
        return data

    ad_sets = data.get("data", [])

    insights_data = _get(
        f"{_account_id()}/insights",
        {
            "fields": "adset_id,adset_name,spend,impressions,clicks,ctr,cpc,reach,frequency",
            "time_range": json.dumps(time_range),
            "level": "adset",
            "limit": 50,
        },
    )
    insights_by_id = {}
    if "data" in insights_data:
        for row in insights_data["data"]:
            insights_by_id[row.get("adset_id")] = row

    results = []
    for s in ad_sets:
        ins = insights_by_id.get(s["id"], {})
        targeting = s.get("targeting", {})
        age_range = ""
        if "age_min" in targeting or "age_max" in targeting:
            age_range = f"{targeting.get('age_min', '18')}-{targeting.get('age_max', '65+')}"
        results.append({
            "id": s["id"],
            "name": s["name"],
            "status": s["status"],
            "campaign_id": s.get("campaign_id"),
            "optimization_goal": s.get("optimization_goal"),
            "age_range": age_range,
            "spend": ins.get("spend", "0"),
            "impressions": ins.get("impressions", "0"),
            "clicks": ins.get("clicks", "0"),
            "ctr": ins.get("ctr", "0"),
            "reach": ins.get("reach", "0"),
            "frequency": ins.get("frequency", "0"),
        })

    results.sort(key=lambda x: float(x.get("spend", 0) or 0), reverse=True)
    return {"ad_sets": results, "date_range": date_range, "count": len(results)}


def get_ads(ad_set_id: str = None, date_range: str = "last_30d") -> dict:
    """Individual ads with performance metrics."""
    err = _check_config()
    if err:
        return err

    time_range = _date_range_params(date_range)
    filtering = []
    if ad_set_id:
        filtering = [{"field": "adset_id", "operator": "EQUAL", "value": ad_set_id}]

    data = _get(
        f"{_account_id()}/ads",
        {
            "fields": "id,name,status,adset_id,creative{thumbnail_url,title,body}",
            "filtering": json.dumps(filtering) if filtering else "[]",
            "limit": 50,
        },
    )
    if "error" in data:
        return data

    ads = data.get("data", [])
    insights_data = _get(
        f"{_account_id()}/insights",
        {
            "fields": "ad_id,ad_name,spend,impressions,clicks,ctr,cpc,reach",
            "time_range": json.dumps(time_range),
            "level": "ad",
            "limit": 50,
        },
    )
    insights_by_id = {}
    if "data" in insights_data:
        for row in insights_data["data"]:
            insights_by_id[row.get("ad_id")] = row

    results = []
    for ad in ads:
        ins = insights_by_id.get(ad["id"], {})
        creative = ad.get("creative", {})
        results.append({
            "id": ad["id"],
            "name": ad["name"],
            "status": ad["status"],
            "adset_id": ad.get("adset_id"),
            "headline": creative.get("title", ""),
            "body": creative.get("body", ""),
            "thumbnail_url": creative.get("thumbnail_url", ""),
            "spend": ins.get("spend", "0"),
            "impressions": ins.get("impressions", "0"),
            "clicks": ins.get("clicks", "0"),
            "ctr": ins.get("ctr", "0"),
            "cpc": ins.get("cpc", "0"),
        })

    results.sort(key=lambda x: float(x.get("spend", 0) or 0), reverse=True)
    return {"ads": results, "date_range": date_range, "count": len(results)}


def get_insights(
    object_id: str,
    object_level: str = "campaign",
    date_range: str = "last_30d",
    breakdowns: list = None,
) -> dict:
    """Breakdown report for a specific campaign, ad set, or ad."""
    err = _check_config()
    if err:
        return err

    valid_levels = {"campaign", "adset", "ad"}
    if object_level not in valid_levels:
        return {"error": "INVALID_LEVEL", "valid_levels": list(valid_levels)}

    time_range = _date_range_params(date_range)
    params = {
        "fields": "spend,impressions,clicks,ctr,cpc,cpm,reach,frequency,actions,cost_per_action_type",
        "time_range": json.dumps(time_range),
        "level": object_level,
    }
    if breakdowns:
        valid_breakdowns = {"age", "gender", "placement", "device_platform", "publisher_platform"}
        clean = [b for b in breakdowns if b in valid_breakdowns]
        if clean:
            params["breakdowns"] = ",".join(clean)

    data = _get(f"{object_id}/insights", params)
    if "error" in data:
        return data

    return {
        "object_id": object_id,
        "object_level": object_level,
        "date_range": date_range,
        "breakdowns": breakdowns or [],
        "data": data.get("data", []),
    }


def check_connection() -> dict:
    """Test Meta credentials and return connection status."""
    missing = []
    if not os.environ.get("META_ACCESS_TOKEN"):
        missing.append("META_ACCESS_TOKEN")
    if not os.environ.get("META_AD_ACCOUNT_ID"):
        missing.append("META_AD_ACCOUNT_ID")

    if missing:
        return {
            "platform": "meta",
            "configured": False,
            "missing_vars": missing,
            "hint": "Run /ads-connect to set up Meta Ads.",
        }

    if not HAS_REQUESTS:
        return {
            "platform": "meta",
            "configured": False,
            "missing_vars": [],
            "hint": "Run: pip install requests",
        }

    # Live test: fetch account name
    data = _get(_account_id(), {"fields": "name,currency,timezone_name"})
    if "error" in data:
        return {
            "platform": "meta",
            "configured": True,
            "token_test": "failed",
            "error": data,
        }

    return {
        "platform": "meta",
        "configured": True,
        "token_test": "ok",
        "account_id": _account_id(),
        "account_name": data.get("name"),
        "currency": data.get("currency"),
        "timezone": data.get("timezone_name"),
    }
