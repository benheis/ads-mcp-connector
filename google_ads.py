#!/usr/bin/env python3
"""
Google Ads API client for ads-mcp-connector.
Uses google-ads Python library with GAQL queries.
Credentials loaded from environment — no yaml file required.
"""

from __future__ import annotations

import json
import os

try:
    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException
    HAS_GOOGLE_ADS = True
except ImportError:
    HAS_GOOGLE_ADS = False

NOT_CONFIGURED = {
    "error": "GOOGLE_NOT_CONFIGURED",
    "hint": "Run /ads-connect in Claude Code to set up Google Ads.",
    "missing": [],
}

REQUIRED_VARS = [
    "GOOGLE_DEVELOPER_TOKEN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "GOOGLE_CUSTOMER_ID",
]


def _check_config() -> dict | None:
    """Return error dict if credentials are missing, None if all good."""
    if not HAS_GOOGLE_ADS:
        return {
            "error": "MISSING_DEPENDENCY",
            "hint": "Run: pip install google-ads google-auth-oauthlib",
        }
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    if missing:
        err = dict(NOT_CONFIGURED)
        err["missing"] = missing
        return err
    return None


def _customer_id() -> str:
    """Return customer ID without dashes."""
    return os.environ.get("GOOGLE_CUSTOMER_ID", "").replace("-", "")


def _login_customer_id() -> str | None:
    return os.environ.get("GOOGLE_LOGIN_CUSTOMER_ID", "").replace("-", "") or None


def _build_client() -> "GoogleAdsClient":
    """Build a GoogleAdsClient from environment variables."""
    config = {
        "developer_token": os.environ["GOOGLE_DEVELOPER_TOKEN"],
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }
    login_id = _login_customer_id()
    if login_id:
        config["login_customer_id"] = login_id
    return GoogleAdsClient.load_from_dict(config)


def _date_range_gaql(date_range: str) -> str:
    """Convert friendly date range to GAQL date range clause."""
    mapping = {
        "today": "TODAY",
        "yesterday": "YESTERDAY",
        "last_7d": "LAST_7_DAYS",
        "last_14d": "LAST_14_DAYS",
        "last_30d": "LAST_30_DAYS",
        "last_90d": "LAST_90_DAYS",
        "this_month": "THIS_MONTH",
        "last_month": "LAST_MONTH",
        "last_week": "LAST_WEEK_SUN_SAT",
    }
    return mapping.get(date_range, "LAST_30_DAYS")


def _run_query(gaql: str) -> list | dict:
    """Execute a GAQL query and return rows as list of dicts."""
    try:
        client = _build_client()
        service = client.get_service("GoogleAdsService")
        response = service.search_stream(customer_id=_customer_id(), query=gaql)
        rows = []
        for batch in response:
            for row in batch.results:
                rows.append(row)
        return rows
    except GoogleAdsException as ex:
        errors = []
        for error in ex.failure.errors:
            errors.append({
                "code": error.error_code.WhichOneof("error_code"),
                "message": error.message,
            })
        return {"error": "GOOGLE_ADS_API_ERROR", "details": errors}
    except Exception as e:
        msg = str(e)
        if "UNAUTHENTICATED" in msg or "invalid_grant" in msg:
            return {
                "error": "GOOGLE_TOKEN_INVALID",
                "hint": "Your Google credentials may be invalid. Run /ads-connect to reconfigure.",
                "detail": msg,
            }
        return {"error": "GOOGLE_REQUEST_FAILED", "message": msg}


# ─── Tool implementations ──────────────────────────────────────────────────────


def get_account_overview(date_range: str = "last_30d") -> dict:
    """Account-level metrics: cost, conversions, ROAS, impression share."""
    err = _check_config()
    if err:
        return err

    gaql_range = _date_range_gaql(date_range)
    gaql = f"""
        SELECT
            customer.id,
            customer.descriptive_name,
            customer.currency_code,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions,
            metrics.conversions_value,
            metrics.search_impression_share,
            metrics.ctr
        FROM customer
        WHERE segments.date DURING {gaql_range}
    """
    rows = _run_query(gaql)
    if isinstance(rows, dict) and "error" in rows:
        return rows
    if not rows:
        return {"error": "NO_DATA", "hint": "No data found for this date range."}

    row = rows[0]
    metrics = row.metrics
    customer = row.customer
    cost = metrics.cost_micros / 1_000_000
    conv_value = metrics.conversions_value
    roas = round(conv_value / cost, 2) if cost > 0 else 0

    return {
        "customer_id": _customer_id(),
        "account_name": customer.descriptive_name,
        "currency": customer.currency_code,
        "date_range": date_range,
        "cost": round(cost, 2),
        "impressions": metrics.impressions,
        "clicks": metrics.clicks,
        "ctr": round(metrics.ctr * 100, 2),
        "conversions": round(metrics.conversions, 1),
        "conversions_value": round(conv_value, 2),
        "roas": roas,
        "search_impression_share": round(metrics.search_impression_share * 100, 1),
    }


def get_campaigns(date_range: str = "last_30d", status_filter: str = "ENABLED") -> dict:
    """All campaigns with cost, clicks, conversions, ROAS."""
    err = _check_config()
    if err:
        return err

    gaql_range = _date_range_gaql(date_range)
    status_clause = ""
    if status_filter and status_filter != "ALL":
        status_clause = f"AND campaign.status = '{status_filter}'"

    gaql = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.advertising_channel_type,
            campaign.bidding_strategy_type,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.conversions,
            metrics.conversions_value
        FROM campaign
        WHERE segments.date DURING {gaql_range}
            {status_clause}
        ORDER BY metrics.cost_micros DESC
        LIMIT 50
    """
    rows = _run_query(gaql)
    if isinstance(rows, dict) and "error" in rows:
        return rows

    campaigns = []
    for row in rows:
        cost = row.metrics.cost_micros / 1_000_000
        conv_value = row.metrics.conversions_value
        roas = round(conv_value / cost, 2) if cost > 0 else 0
        campaigns.append({
            "id": str(row.campaign.id),
            "name": row.campaign.name,
            "status": row.campaign.status.name,
            "channel": row.campaign.advertising_channel_type.name,
            "bidding_strategy": row.campaign.bidding_strategy_type.name,
            "cost": round(cost, 2),
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "ctr": round(row.metrics.ctr * 100, 2),
            "conversions": round(row.metrics.conversions, 1),
            "roas": roas,
        })

    return {"campaigns": campaigns, "date_range": date_range, "count": len(campaigns)}


def get_ad_groups(campaign_id: str = None, date_range: str = "last_30d") -> dict:
    """Ad groups with bid, performance metrics."""
    err = _check_config()
    if err:
        return err

    gaql_range = _date_range_gaql(date_range)
    campaign_clause = ""
    if campaign_id:
        campaign_clause = f"AND campaign.id = {campaign_id}"

    gaql = f"""
        SELECT
            ad_group.id,
            ad_group.name,
            ad_group.status,
            ad_group.type,
            campaign.id,
            campaign.name,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.conversions
        FROM ad_group
        WHERE segments.date DURING {gaql_range}
            AND ad_group.status = 'ENABLED'
            {campaign_clause}
        ORDER BY metrics.cost_micros DESC
        LIMIT 50
    """
    rows = _run_query(gaql)
    if isinstance(rows, dict) and "error" in rows:
        return rows

    ad_groups = []
    for row in rows:
        cost = row.metrics.cost_micros / 1_000_000
        ad_groups.append({
            "id": str(row.ad_group.id),
            "name": row.ad_group.name,
            "status": row.ad_group.status.name,
            "type": row.ad_group.type_.name,
            "campaign_id": str(row.campaign.id),
            "campaign_name": row.campaign.name,
            "cost": round(cost, 2),
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "ctr": round(row.metrics.ctr * 100, 2),
            "conversions": round(row.metrics.conversions, 1),
        })

    return {"ad_groups": ad_groups, "date_range": date_range, "count": len(ad_groups)}


def get_keywords(
    ad_group_id: str = None,
    date_range: str = "last_30d",
    min_impressions: int = 0,
) -> dict:
    """Keywords with Quality Score, avg CPC, performance."""
    err = _check_config()
    if err:
        return err

    gaql_range = _date_range_gaql(date_range)
    ad_group_clause = f"AND ad_group.id = {ad_group_id}" if ad_group_id else ""
    impression_clause = f"AND metrics.impressions >= {min_impressions}" if min_impressions else ""

    gaql = f"""
        SELECT
            ad_group_criterion.keyword.text,
            ad_group_criterion.keyword.match_type,
            ad_group_criterion.quality_info.quality_score,
            ad_group.name,
            campaign.name,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.average_cpc,
            metrics.conversions
        FROM keyword_view
        WHERE segments.date DURING {gaql_range}
            AND ad_group_criterion.status = 'ENABLED'
            {ad_group_clause}
            {impression_clause}
        ORDER BY metrics.impressions DESC
        LIMIT 100
    """
    rows = _run_query(gaql)
    if isinstance(rows, dict) and "error" in rows:
        return rows

    keywords = []
    for row in rows:
        keywords.append({
            "keyword": row.ad_group_criterion.keyword.text,
            "match_type": row.ad_group_criterion.keyword.match_type.name,
            "quality_score": row.ad_group_criterion.quality_info.quality_score,
            "ad_group": row.ad_group.name,
            "campaign": row.campaign.name,
            "cost": round(row.metrics.cost_micros / 1_000_000, 2),
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "ctr": round(row.metrics.ctr * 100, 2),
            "avg_cpc": round(row.metrics.average_cpc / 1_000_000, 2),
            "conversions": round(row.metrics.conversions, 1),
        })

    return {"keywords": keywords, "date_range": date_range, "count": len(keywords)}


def get_search_terms(
    campaign_id: str = None,
    date_range: str = "last_30d",
    min_impressions: int = 5,
) -> dict:
    """Actual search terms triggering ads — key for negative keyword discovery."""
    err = _check_config()
    if err:
        return err

    gaql_range = _date_range_gaql(date_range)
    campaign_clause = f"AND campaign.id = {campaign_id}" if campaign_id else ""
    impression_clause = f"AND metrics.impressions >= {min_impressions}"

    gaql = f"""
        SELECT
            search_term_view.search_term,
            search_term_view.status,
            campaign.name,
            ad_group.name,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.cost_micros,
            metrics.conversions
        FROM search_term_view
        WHERE segments.date DURING {gaql_range}
            {campaign_clause}
            {impression_clause}
        ORDER BY metrics.impressions DESC
        LIMIT 100
    """
    rows = _run_query(gaql)
    if isinstance(rows, dict) and "error" in rows:
        return rows

    terms = []
    for row in rows:
        terms.append({
            "search_term": row.search_term_view.search_term,
            "status": row.search_term_view.status.name,
            "campaign": row.campaign.name,
            "ad_group": row.ad_group.name,
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "ctr": round(row.metrics.ctr * 100, 2),
            "cost": round(row.metrics.cost_micros / 1_000_000, 2),
            "conversions": round(row.metrics.conversions, 1),
        })

    return {"search_terms": terms, "date_range": date_range, "count": len(terms)}


def check_connection() -> dict:
    """Test Google Ads credentials and return connection status."""
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    if not HAS_GOOGLE_ADS:
        return {
            "platform": "google",
            "configured": False,
            "missing_vars": ["google-ads package not installed"],
            "hint": "Run: pip install google-ads",
        }
    if missing:
        return {
            "platform": "google",
            "configured": True if not missing else False,
            "missing_vars": missing,
            "hint": "Run /ads-connect to set up Google Ads.",
        }

    # Live test: fetch customer name
    gaql = "SELECT customer.id, customer.descriptive_name, customer.currency_code FROM customer LIMIT 1"
    rows = _run_query(gaql)
    if isinstance(rows, dict) and "error" in rows:
        return {
            "platform": "google",
            "configured": True,
            "token_test": "failed",
            "error": rows,
        }

    customer = rows[0].customer if rows else None
    return {
        "platform": "google",
        "configured": True,
        "token_test": "ok",
        "customer_id": _customer_id(),
        "account_name": customer.descriptive_name if customer else "unknown",
        "currency": customer.currency_code if customer else "unknown",
    }
