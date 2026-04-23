#!/usr/bin/env python3
"""
Meta Ads API client for ads-mcp-connector.
Uses Meta Graph API v19.0 via requests.
Credentials loaded from environment (META_ACCESS_TOKEN, META_AD_ACCOUNT_ID).
"""

from __future__ import annotations

import calendar
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
    """Convert a date range to Meta API time_range params.

    Accepts:
    - Preset strings: today, yesterday, last_7d, last_14d, last_30d, last_90d,
      last_6_months, last_12_months, this_month, last_month
    - Custom JSON: '{"since": "2025-01-01", "until": "2025-03-31"}'

    Note: Meta reports dates in the ad account's timezone, which may differ from
    the server's local time. A 'until: today' request can show future-looking
    date_stop values when the account timezone is ahead of the server timezone.
    """
    # Support custom {"since": "...", "until": "..."} passed as a JSON string
    stripped = date_range.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            if "since" in parsed and "until" in parsed:
                return {"since": parsed["since"], "until": parsed["until"]}
        except (json.JSONDecodeError, KeyError):
            pass

    today = datetime.today()
    ranges = {
        "today":          (today, today),
        "yesterday":      (today - timedelta(1), today - timedelta(1)),
        "last_7d":        (today - timedelta(7), today),
        "last_14d":       (today - timedelta(14), today),
        "last_30d":       (today - timedelta(30), today),
        "last_90d":       (today - timedelta(90), today),
        "last_6_months":  (today - timedelta(183), today),
        "last_12_months": (today - timedelta(365), today),
        "this_month":     (today.replace(day=1), today),
        "last_month":     (
            (today.replace(day=1) - timedelta(1)).replace(day=1),
            today.replace(day=1) - timedelta(1),
        ),
    }
    since, until = ranges.get(date_range, ranges["last_30d"])
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


def _get_paged(endpoint: str, params: dict, max_rows: int = 500) -> list:
    """Fetch all cursor-paginated rows from an endpoint, up to max_rows."""
    rows = []
    params = dict(params)
    while True:
        data = _get(endpoint, params)
        if "error" in data:
            break
        rows.extend(data.get("data", []))
        after = data.get("paging", {}).get("cursors", {}).get("after")
        if not after or "next" not in data.get("paging", {}) or len(rows) >= max_rows:
            break
        params["after"] = after
    return rows[:max_rows]


def _filter_cpa(cost_per_action_list: list, conversion_event: str = None) -> tuple:
    """Return (filtered_list, cpa_value) for a specific conversion event.

    conversion_event is matched loosely against action_type substrings so that
    e.g. 'purchase' matches 'offsite_conversion.fb_pixel_purchase' and 'omni_purchase'.
    Returns the full list and None if no conversion_event is given.
    """
    if not conversion_event or not cost_per_action_list:
        return cost_per_action_list or [], None
    key = conversion_event.lower().replace(" ", "_")
    filtered = [row for row in cost_per_action_list if key in row.get("action_type", "").lower()]
    cpa_value = filtered[0]["value"] if filtered else "0"
    return filtered, cpa_value


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


def get_ads(
    ad_set_id: str = None,
    date_range: str = "last_30d",
    status_filter: str = "ALL",
    conversion_event: str = None,
) -> dict:
    """Individual ads with performance metrics and created_time.

    status_filter: ACTIVE, PAUSED, or ALL (default ALL — diagnostic needs paused ads too).
    conversion_event: if provided, filters cost_per_action_type to matching entries and
        adds a top-level 'cpa' field with just that event's cost.
    date_range: preset string OR custom JSON e.g. '{"since":"2025-01-01","until":"2025-03-31"}'.
    """
    err = _check_config()
    if err:
        return err

    time_range = _date_range_params(date_range)
    filtering = []
    if ad_set_id:
        filtering.append({"field": "adset_id", "operator": "EQUAL", "value": ad_set_id})
    if status_filter and status_filter != "ALL":
        filtering.append({"field": "effective_status", "operator": "IN", "value": [status_filter]})

    # Step 1: insights first — the reliable source for spend and CPA.
    # Starting from the ads metadata endpoint and joining insights fails because
    # the two paginated lists rarely align (different ordering, different active sets).
    ins_params = {
        "fields": "ad_id,ad_name,spend,impressions,clicks,ctr,cpc,reach,cost_per_action_type",
        "time_range": json.dumps(time_range),
        "level": "ad",
        "limit": 200,
    }
    if filtering:
        ins_params["filtering"] = json.dumps(filtering)

    insights_rows = _get_paged(f"{_account_id()}/insights", ins_params)

    # Step 2: fetch ad metadata (created_time, status) for the same scope.
    meta_params = {
        "fields": "id,name,status,adset_id,created_time",
        "filtering": json.dumps(filtering) if filtering else "[]",
        "limit": 200,
    }
    meta_rows = _get_paged(f"{_account_id()}/ads", meta_params)
    ad_meta = {ad["id"]: ad for ad in meta_rows}

    # Step 3: merge — insights rows are the authority on which ads ran.
    results = []
    for ins in insights_rows:
        ad_id = ins.get("ad_id", "")
        meta = ad_meta.get(ad_id, {})
        raw_cpa_list = ins.get("cost_per_action_type", [])
        filtered_cpa, cpa_value = _filter_cpa(raw_cpa_list, conversion_event)
        row = {
            "id": ad_id,
            "name": ins.get("ad_name") or meta.get("name", ""),
            "status": meta.get("status", ""),
            "adset_id": meta.get("adset_id", ""),
            "created_time": meta.get("created_time", ""),
            "spend": ins.get("spend", "0"),
            "impressions": ins.get("impressions", "0"),
            "clicks": ins.get("clicks", "0"),
            "ctr": ins.get("ctr", "0"),
            "cpc": ins.get("cpc", "0"),
            "cost_per_action_type": filtered_cpa,
        }
        if conversion_event:
            row["cpa"] = cpa_value
        results.append(row)

    results.sort(key=lambda x: float(x.get("spend", 0) or 0), reverse=True)
    return {"ads": results, "date_range": date_range, "count": len(results)}


def get_insights(
    object_id: str,
    object_level: str = "campaign",
    date_range: str = "last_30d",
    breakdowns: list = None,
    conversion_event: str = None,
    time_increment: str = None,
) -> dict:
    """Breakdown report for a specific campaign, ad set, or ad.

    conversion_event: if provided, filters cost_per_action_type to matching entries
        and adds a top-level 'cpa' field per row for that event's cost.
    date_range: preset string OR custom JSON e.g. '{"since":"2025-01-01","until":"2025-03-31"}'.
    time_increment: optional time bucketing — "monthly", "weekly", or "1" (daily).
        When set, each row includes a 'date_start' / 'date_stop' instead of totals.
    """
    err = _check_config()
    if err:
        return err

    valid_levels = {"campaign", "adset", "ad"}
    if object_level not in valid_levels:
        return {"error": "INVALID_LEVEL", "valid_levels": list(valid_levels)}

    time_range = _date_range_params(date_range)
    # Include ad_id and ad_name when querying at ad level so rows aren't anonymous
    id_fields = "ad_id,ad_name," if object_level == "ad" else ""
    params = {
        "fields": f"{id_fields}spend,impressions,clicks,ctr,cpc,cpm,reach,frequency,actions,cost_per_action_type",
        "time_range": json.dumps(time_range),
        "level": object_level,
    }
    if breakdowns:
        valid_breakdowns = {"age", "gender", "placement", "device_platform", "publisher_platform"}
        clean = [b for b in breakdowns if b in valid_breakdowns]
        if clean:
            params["breakdowns"] = ",".join(clean)
    if time_increment:
        params["time_increment"] = time_increment

    data = _get(f"{object_id}/insights", params)
    if "error" in data:
        return data

    rows = data.get("data", [])
    if conversion_event:
        for row in rows:
            filtered, cpa_value = _filter_cpa(row.get("cost_per_action_type", []), conversion_event)
            row["cost_per_action_type"] = filtered
            row["cpa"] = cpa_value

    return {
        "object_id": object_id,
        "object_level": object_level,
        "date_range": date_range,
        "breakdowns": breakdowns or [],
        "time_increment": time_increment,
        "data": rows,
    }


def get_monthly_reach(months: int = 13) -> dict:
    """Monthly reach, impressions, and spend for the last N months. Used for rolling reach analysis."""
    err = _check_config()
    if err:
        return err

    today = datetime.today()
    results = []
    for i in range(months - 1, -1, -1):
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        since = datetime(year, month, 1)
        _, last_day = calendar.monthrange(year, month)
        until = datetime(year, month, last_day)
        if until > today:
            until = today

        time_range = {
            "since": since.strftime("%Y-%m-%d"),
            "until": until.strftime("%Y-%m-%d"),
        }
        data = _get(
            f"{_account_id()}/insights",
            {
                "fields": "reach,impressions,spend",
                "time_range": json.dumps(time_range),
                "level": "account",
            },
        )
        row = {
            "month": since.strftime("%Y-%m"),
            "since": time_range["since"],
            "until": time_range["until"],
        }
        if "data" in data and data["data"]:
            d = data["data"][0]
            row["reach"] = int(d.get("reach", 0) or 0)
            row["impressions"] = int(d.get("impressions", 0) or 0)
            row["spend"] = float(d.get("spend", 0) or 0)
        elif "error" in data:
            row["error"] = data["error"]
            row["reach"] = 0
            row["impressions"] = 0
            row["spend"] = 0.0
        else:
            row["reach"] = 0
            row["impressions"] = 0
            row["spend"] = 0.0
        results.append(row)

    return {"months": results, "count": len(results)}


def get_ad_monthly_spend(months: int = 6, status_filter: str = "ALL") -> dict:
    """Per-ad spend broken down by calendar month for the last N months.

    Returns one entry per ad with a monthly_spend array — the primitive needed for
    creative cohort analysis. Internally makes one insights call with time_increment=monthly
    at ad level, then enriches with ad metadata (name, created_time, status).
    """
    err = _check_config()
    if err:
        return err

    today = datetime.today()
    since_dt = datetime(today.year, today.month, 1)
    for _ in range(months - 1):
        m = since_dt.month - 1
        y = since_dt.year
        if m == 0:
            m = 12
            y -= 1
        since_dt = datetime(y, m, 1)
    since = since_dt.strftime("%Y-%m-%d")
    until = today.strftime("%Y-%m-%d")

    time_range = {"since": since, "until": until}
    params = {
        "fields": "ad_id,ad_name,spend",
        "time_range": json.dumps(time_range),
        "level": "ad",
        "time_increment": "monthly",
        "limit": 500,
    }
    data = _get(f"{_account_id()}/insights", params)
    if "error" in data:
        return data

    # Collect all pages
    rows = list(data.get("data", []))
    paging = data.get("paging", {})
    while paging.get("next"):
        page = _get_paged(paging["next"], {})
        if "error" in page:
            break
        rows.extend(page.get("data", []))
        paging = page.get("paging", {})

    # Group rows by ad_id → monthly_spend list
    ads: dict = {}
    for row in rows:
        ad_id = row.get("ad_id", "")
        if not ad_id:
            continue
        if ad_id not in ads:
            ads[ad_id] = {
                "ad_id": ad_id,
                "ad_name": row.get("ad_name", ""),
                "monthly_spend": [],
            }
        month_str = row.get("date_start", "")[:7]  # YYYY-MM
        ads[ad_id]["monthly_spend"].append({
            "month": month_str,
            "spend": float(row.get("spend", 0) or 0),
        })

    # Enrich with created_time and status from ads metadata endpoint
    if ads:
        meta_params = {
            "fields": "id,name,created_time,effective_status",
            "filtering": json.dumps([{"field": "effective_status", "operator": "IN",
                                      "value": ["ACTIVE", "PAUSED", "ARCHIVED",
                                                "DELETED", "DISAPPROVED", "PENDING_REVIEW"]}]),
            "limit": 500,
        }
        meta_data = _get(f"{_account_id()}/ads", meta_params)
        meta_lookup = {}
        for ad in meta_data.get("data", []):
            meta_lookup[ad["id"]] = ad
        for ad_id, ad in ads.items():
            meta = meta_lookup.get(ad_id, {})
            ad["created_time"] = meta.get("created_time", "")
            ad["status"] = meta.get("effective_status", "")

    result = list(ads.values())
    if status_filter and status_filter != "ALL":
        result = [a for a in result if a.get("status", "").upper() == status_filter.upper()]

    return {"ads": result, "count": len(result), "date_range": {"since": since, "until": until}}


# ─── Write helpers ────────────────────────────────────────────────────────────


def _post(endpoint: str, data: dict) -> dict:
    """Make a Graph API POST request. Returns parsed JSON or error dict."""
    data = dict(data)
    data["access_token"] = _token()
    try:
        resp = requests.post(f"{GRAPH_API_BASE}/{endpoint}", data=data, timeout=30)
        result = resp.json()
        if "error" in result:
            code = result["error"].get("code")
            msg = result["error"].get("message", "Unknown Meta API error")
            if code == 190:
                return {"error": "META_TOKEN_EXPIRED", "message": msg,
                        "hint": "Your Meta token has expired. Run /ads-connect to renew it."}
            return {"error": "META_API_ERROR", "code": code, "message": msg}
        return result
    except requests.exceptions.Timeout:
        return {"error": "TIMEOUT", "hint": "Meta API request timed out. Try again."}
    except Exception as e:
        return {"error": "REQUEST_FAILED", "message": str(e)}


def _upload(endpoint: str, files: dict, data: dict) -> dict:
    """Make a multipart POST request (for image/video uploads)."""
    data = dict(data)
    data["access_token"] = _token()
    try:
        resp = requests.post(f"{GRAPH_API_BASE}/{endpoint}", files=files, data=data, timeout=120)
        result = resp.json()
        if "error" in result:
            return {"error": "META_API_ERROR", "message": result["error"].get("message", "Upload failed")}
        return result
    except requests.exceptions.Timeout:
        return {"error": "TIMEOUT", "hint": "Upload timed out — try a smaller file or check your connection."}
    except Exception as e:
        return {"error": "REQUEST_FAILED", "message": str(e)}


# ─── Write implementations ─────────────────────────────────────────────────────


def update_campaign_status(campaign_id: str, status: str) -> dict:
    """Pause or enable a campaign. status: ACTIVE or PAUSED."""
    err = _check_config()
    if err:
        return err
    result = _post(campaign_id, {"status": status})
    if result.get("success"):
        return {"updated": campaign_id, "type": "campaign", "status": status}
    return result


def update_ad_set_status(ad_set_id: str, status: str) -> dict:
    """Pause or enable an ad set. status: ACTIVE or PAUSED."""
    err = _check_config()
    if err:
        return err
    result = _post(ad_set_id, {"status": status})
    if result.get("success"):
        return {"updated": ad_set_id, "type": "ad_set", "status": status}
    return result


def update_ad_status(ad_id: str, status: str) -> dict:
    """Pause or enable an individual ad. status: ACTIVE or PAUSED."""
    err = _check_config()
    if err:
        return err
    result = _post(ad_id, {"status": status})
    if result.get("success"):
        return {"updated": ad_id, "type": "ad", "status": status}
    return result


def update_budget(
    object_id: str,
    object_type: str,
    budget_type: str,
    amount_dollars: float,
    daily_min_dollars: float = None,
    daily_max_dollars: float = None,
) -> dict:
    """Update budget on a campaign (CBO) or ad set.

    object_type: 'campaign' or 'ad_set'
    budget_type: 'daily' or 'lifetime'
    amount_dollars: budget in dollars (converted to cents internally)
    daily_min_dollars / daily_max_dollars: CBO ad-set spend constraints (ad_set only)
    """
    err = _check_config()
    if err:
        return err

    payload = {}
    field = "daily_budget" if budget_type == "daily" else "lifetime_budget"
    payload[field] = int(amount_dollars * 100)

    if object_type == "ad_set":
        if daily_min_dollars is not None:
            payload["daily_min_spend_target"] = int(daily_min_dollars * 100)
        if daily_max_dollars is not None:
            payload["daily_max_spend_cap"] = int(daily_max_dollars * 100)

    result = _post(object_id, payload)
    if result.get("success"):
        return {"updated": object_id, "type": object_type, "budget_type": budget_type,
                "amount_dollars": amount_dollars}
    return result


def create_campaign(
    name: str,
    objective: str,
    budget_type: str,
    amount_dollars: float,
    status: str = "PAUSED",
    special_ad_categories: list = None,
) -> dict:
    """Create a new Meta Ads campaign.

    objective: OUTCOME_TRAFFIC, OUTCOME_LEADS, OUTCOME_SALES, OUTCOME_AWARENESS,
               OUTCOME_ENGAGEMENT, OUTCOME_APP_PROMOTION
    budget_type: 'daily' or 'lifetime'
    status: ACTIVE or PAUSED (default PAUSED — always review before activating)
    """
    err = _check_config()
    if err:
        return err

    budget_field = "daily_budget" if budget_type == "daily" else "lifetime_budget"
    payload = {
        "name": name,
        "objective": objective,
        "status": status,
        "special_ad_categories": json.dumps(special_ad_categories or []),
        budget_field: int(amount_dollars * 100),
    }
    result = _post(f"{_account_id()}/campaigns", payload)
    if "id" in result:
        return {"campaign_id": result["id"], "name": name, "objective": objective,
                "status": status, budget_field: amount_dollars}
    return result


def create_ad_set(
    campaign_id: str,
    name: str,
    optimization_goal: str,
    billing_event: str = "IMPRESSIONS",
    bid_strategy: str = "LOWEST_COST_WITHOUT_CAP",
    daily_budget_dollars: float = None,
    lifetime_budget_dollars: float = None,
    targeting: dict = None,
    start_time: str = None,
    end_time: str = None,
    status: str = "PAUSED",
) -> dict:
    """Create a new ad set inside a campaign.

    optimization_goal: OFFSITE_CONVERSIONS, LINK_CLICKS, REACH, IMPRESSIONS,
                       LANDING_PAGE_VIEWS, LEAD_GENERATION
    bid_strategy: LOWEST_COST_WITHOUT_CAP, LOWEST_COST_WITH_BID_CAP, COST_CAP
    targeting: dict matching Meta targeting spec (geo, age, interests, etc.)
    start_time / end_time: ISO 8601 strings (e.g. '2025-05-01T00:00:00-0500')
    """
    err = _check_config()
    if err:
        return err

    payload = {
        "campaign_id": campaign_id,
        "name": name,
        "optimization_goal": optimization_goal,
        "billing_event": billing_event,
        "bid_strategy": bid_strategy,
        "status": status,
        "targeting": json.dumps(targeting or {"geo_locations": {"countries": ["US"]}}),
    }
    if daily_budget_dollars is not None:
        payload["daily_budget"] = int(daily_budget_dollars * 100)
    elif lifetime_budget_dollars is not None:
        payload["lifetime_budget"] = int(lifetime_budget_dollars * 100)
    if start_time:
        payload["start_time"] = start_time
    if end_time:
        payload["end_time"] = end_time

    result = _post(f"{_account_id()}/adsets", payload)
    if "id" in result:
        return {"ad_set_id": result["id"], "name": name, "campaign_id": campaign_id,
                "optimization_goal": optimization_goal, "status": status}
    return result


def create_ad(
    ad_set_id: str,
    name: str,
    creative_id: str,
    status: str = "PAUSED",
) -> dict:
    """Create an ad linking to an existing ad creative.

    creative_id: returned by create_ad_creative or visible in Ads Manager.
    status: PAUSED (default) — review creative before activating.
    """
    err = _check_config()
    if err:
        return err

    payload = {
        "adset_id": ad_set_id,
        "name": name,
        "creative": json.dumps({"creative_id": creative_id}),
        "status": status,
    }
    result = _post(f"{_account_id()}/ads", payload)
    if "id" in result:
        return {"ad_id": result["id"], "name": name, "ad_set_id": ad_set_id,
                "creative_id": creative_id, "status": status}
    return result


def upload_image(file_path: str) -> dict:
    """Upload a static image to the ad account's image library.

    file_path: absolute path to the image file on the local machine.
    Returns image_hash — use this in create_ad_creative.
    Supported formats: JPG, PNG, GIF. Recommended: 1200x628px for link ads.
    """
    err = _check_config()
    if err:
        return err

    import os as _os
    if not _os.path.exists(file_path):
        return {"error": "FILE_NOT_FOUND", "file_path": file_path}

    filename = _os.path.basename(file_path)
    try:
        with open(file_path, "rb") as f:
            result = _upload(
                f"{_account_id()}/adimages",
                files={"filename": (filename, f)},
                data={},
            )
    except OSError as e:
        return {"error": "FILE_READ_ERROR", "message": str(e)}

    if "error" in result:
        return result

    images = result.get("images", {})
    if filename in images:
        img = images[filename]
        return {"image_hash": img.get("hash"), "url": img.get("url"),
                "width": img.get("width"), "height": img.get("height"), "filename": filename}
    return {"error": "UNEXPECTED_RESPONSE", "raw": result}


def upload_video(file_path: str, title: str = None) -> dict:
    """Upload a video to the ad account's video library.

    file_path: absolute path to the video file on the local machine.
    Returns video_id — use this in create_ad_creative.
    Supported formats: MP4, MOV. Max file size: 4GB.
    Note: large videos may take time; the API returns immediately with a video_id
    while encoding happens asynchronously.
    """
    err = _check_config()
    if err:
        return err

    import os as _os
    if not _os.path.exists(file_path):
        return {"error": "FILE_NOT_FOUND", "file_path": file_path}

    filename = _os.path.basename(file_path)
    extra = {}
    if title:
        extra["title"] = title

    try:
        with open(file_path, "rb") as f:
            result = _upload(
                f"{_account_id()}/advideos",
                files={"source": (filename, f)},
                data=extra,
            )
    except OSError as e:
        return {"error": "FILE_READ_ERROR", "message": str(e)}

    if "error" in result:
        return result
    if "id" in result:
        return {"video_id": result["id"], "filename": filename,
                "note": "Video is processing — encoding may take a few minutes before it's usable in creatives."}
    return {"error": "UNEXPECTED_RESPONSE", "raw": result}


def create_ad_creative(
    name: str,
    page_id: str,
    link_url: str,
    message: str,
    headline: str,
    description: str = "",
    call_to_action_type: str = "LEARN_MORE",
    image_hash: str = None,
    video_id: str = None,
) -> dict:
    """Create an ad creative using an uploaded image or video.

    Requires either image_hash (from upload_image) or video_id (from upload_video).
    page_id: your Facebook Page ID — required for all Meta ad creatives.
    call_to_action_type: LEARN_MORE, SHOP_NOW, SIGN_UP, GET_QUOTE, DOWNLOAD,
                         BOOK_TRAVEL, CONTACT_US, WATCH_MORE, APPLY_NOW, GET_OFFER
    Returns creative_id — pass this to create_ad.
    """
    err = _check_config()
    if err:
        return err

    if not image_hash and not video_id:
        return {"error": "MISSING_ASSET", "hint": "Provide image_hash or video_id."}

    cta = {"type": call_to_action_type}

    if image_hash:
        story_spec = {
            "page_id": page_id,
            "link_data": {
                "image_hash": image_hash,
                "link": link_url,
                "message": message,
                "name": headline,
                "description": description,
                "call_to_action": cta,
            },
        }
    else:
        story_spec = {
            "page_id": page_id,
            "video_data": {
                "video_id": video_id,
                "title": headline,
                "message": message,
                "link_description": description,
                "call_to_action": {**cta, "value": {"link": link_url}},
            },
        }

    payload = {
        "name": name,
        "object_story_spec": json.dumps(story_spec),
    }
    result = _post(f"{_account_id()}/adcreatives", payload)
    if "id" in result:
        return {"creative_id": result["id"], "name": name,
                "asset_type": "image" if image_hash else "video"}
    return result


def get_ad_images() -> dict:
    """List all images in the ad account's image library with their hashes."""
    err = _check_config()
    if err:
        return err

    data = _get(
        f"{_account_id()}/adimages",
        {"fields": "hash,name,url,status,width,height,created_time", "limit": 200},
    )
    if "error" in data:
        return data
    images = data.get("data", [])
    return {"images": images, "count": len(images)}


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
