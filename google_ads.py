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


# ─── Write helper ─────────────────────────────────────────────────────────────


def _mutate(service_name: str, method_name: str, operations: list) -> dict:
    """Execute a mutate operation. Returns resource_names on success, error dict on failure."""
    try:
        client = _build_client()
        service = client.get_service(service_name)
        response = getattr(service, method_name)(customer_id=_customer_id(), operations=operations)
        return {"results": [r.resource_name for r in response.results]}
    except GoogleAdsException as ex:
        return {
            "error": "GOOGLE_ADS_API_ERROR",
            "details": [
                {"code": e.error_code.WhichOneof("error_code"), "message": e.message}
                for e in ex.failure.errors
            ],
        }
    except Exception as e:
        msg = str(e)
        if "UNAUTHENTICATED" in msg or "invalid_grant" in msg:
            return {"error": "GOOGLE_TOKEN_INVALID",
                    "hint": "Run /ads-connect to reconfigure Google credentials.", "detail": msg}
        return {"error": "GOOGLE_REQUEST_FAILED", "message": msg}


# ─── Write implementations ─────────────────────────────────────────────────────


def list_negative_keywords(campaign_id: str = None, ad_group_id: str = None) -> dict:
    """List existing negative keywords with criterion IDs needed for removal."""
    err = _check_config()
    if err:
        return err

    results = []

    # Campaign-level negatives
    if ad_group_id is None:
        campaign_clause = f"AND campaign.id = {campaign_id}" if campaign_id else ""
        gaql = f"""
            SELECT
                campaign_criterion.criterion_id,
                campaign_criterion.keyword.text,
                campaign_criterion.keyword.match_type,
                campaign.id,
                campaign.name
            FROM campaign_criterion
            WHERE campaign_criterion.negative = TRUE
                AND campaign_criterion.type = 'KEYWORD'
                {campaign_clause}
            ORDER BY campaign.name, campaign_criterion.keyword.text
            LIMIT 500
        """
        rows = _run_query(gaql)
        if isinstance(rows, dict) and "error" in rows:
            return rows
        for row in rows:
            results.append({
                "level": "campaign",
                "criterion_id": str(row.campaign_criterion.criterion_id),
                "keyword": row.campaign_criterion.keyword.text,
                "match_type": row.campaign_criterion.keyword.match_type.name,
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
            })

    # Ad group-level negatives
    if campaign_id is None or ad_group_id is not None:
        adgroup_clause = f"AND ad_group.id = {ad_group_id}" if ad_group_id else ""
        campaign_clause2 = f"AND campaign.id = {campaign_id}" if campaign_id and not ad_group_id else ""
        gaql2 = f"""
            SELECT
                ad_group_criterion.criterion_id,
                ad_group_criterion.keyword.text,
                ad_group_criterion.keyword.match_type,
                ad_group.id,
                ad_group.name,
                campaign.id,
                campaign.name
            FROM ad_group_criterion
            WHERE ad_group_criterion.negative = TRUE
                AND ad_group_criterion.type = 'KEYWORD'
                {adgroup_clause}
                {campaign_clause2}
            ORDER BY campaign.name, ad_group.name, ad_group_criterion.keyword.text
            LIMIT 500
        """
        rows2 = _run_query(gaql2)
        if isinstance(rows2, dict) and "error" in rows2:
            return rows2
        for row in rows2:
            results.append({
                "level": "ad_group",
                "criterion_id": str(row.ad_group_criterion.criterion_id),
                "keyword": row.ad_group_criterion.keyword.text,
                "match_type": row.ad_group_criterion.keyword.match_type.name,
                "ad_group_id": str(row.ad_group.id),
                "ad_group_name": row.ad_group.name,
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
            })

    return {"negative_keywords": results, "count": len(results)}


def add_negative_keywords(
    keywords: list,
    match_type: str,
    level: str,
    campaign_id: str,
    ad_group_id: str = None,
) -> dict:
    """Add negative keywords at campaign or ad group level.

    match_type: EXACT, PHRASE, or BROAD
    level: 'campaign' or 'ad_group' (requires ad_group_id if ad_group)
    """
    err = _check_config()
    if err:
        return err

    cid = _customer_id()
    client = _build_client()

    try:
        match_type_enum = client.enums.KeywordMatchTypeEnum[match_type]
    except KeyError:
        return {"error": "INVALID_MATCH_TYPE", "valid": ["EXACT", "PHRASE", "BROAD"]}

    if level == "campaign":
        service = client.get_service("CampaignCriterionService")
        ops = []
        for kw in keywords:
            op = client.get_type("CampaignCriterionOperation")
            c = op.create
            c.campaign = f"customers/{cid}/campaigns/{campaign_id}"
            c.negative = True
            c.keyword.text = kw
            c.keyword.match_type = match_type_enum
            ops.append(op)
        result = _mutate("CampaignCriterionService", "mutate_campaign_criteria", ops)

    elif level == "ad_group":
        if not ad_group_id:
            return {"error": "MISSING_PARAM", "hint": "ad_group_id is required for level='ad_group'"}
        service = client.get_service("AdGroupCriterionService")
        ops = []
        for kw in keywords:
            op = client.get_type("AdGroupCriterionOperation")
            c = op.create
            c.ad_group = f"customers/{cid}/ad_groups/{ad_group_id}"
            c.negative = True
            c.keyword.text = kw
            c.keyword.match_type = match_type_enum
            ops.append(op)
        result = _mutate("AdGroupCriterionService", "mutate_ad_group_criteria", ops)

    else:
        return {"error": "INVALID_LEVEL", "valid": ["campaign", "ad_group"]}

    if "error" in result:
        return result
    return {"added": len(result["results"]), "level": level, "campaign_id": campaign_id,
            "ad_group_id": ad_group_id, "match_type": match_type, "keywords": keywords}


def remove_negative_keywords(
    criterion_ids: list,
    level: str,
    campaign_id: str,
    ad_group_id: str = None,
) -> dict:
    """Remove negative keywords by criterion ID.

    criterion_ids: list of criterion_id values from list_negative_keywords.
    level: 'campaign' or 'ad_group'.
    """
    err = _check_config()
    if err:
        return err

    cid = _customer_id()
    client = _build_client()

    if level == "campaign":
        ops = []
        for crit_id in criterion_ids:
            op = client.get_type("CampaignCriterionOperation")
            op.remove = f"customers/{cid}/campaignCriteria/{campaign_id}~{crit_id}"
            ops.append(op)
        result = _mutate("CampaignCriterionService", "mutate_campaign_criteria", ops)

    elif level == "ad_group":
        if not ad_group_id:
            return {"error": "MISSING_PARAM", "hint": "ad_group_id is required for level='ad_group'"}
        ops = []
        for crit_id in criterion_ids:
            op = client.get_type("AdGroupCriterionOperation")
            op.remove = f"customers/{cid}/adGroupCriteria/{ad_group_id}~{crit_id}"
            ops.append(op)
        result = _mutate("AdGroupCriterionService", "mutate_ad_group_criteria", ops)

    else:
        return {"error": "INVALID_LEVEL", "valid": ["campaign", "ad_group"]}

    if "error" in result:
        return result
    return {"removed": len(result["results"]), "level": level,
            "campaign_id": campaign_id, "criterion_ids": criterion_ids}


def update_campaign_status(campaign_id: str, status: str) -> dict:
    """Pause or enable a campaign. status: ENABLED or PAUSED."""
    err = _check_config()
    if err:
        return err

    cid = _customer_id()
    client = _build_client()
    try:
        status_enum = client.enums.CampaignStatusEnum[status]
    except KeyError:
        return {"error": "INVALID_STATUS", "valid": ["ENABLED", "PAUSED"]}

    op = client.get_type("CampaignOperation")
    c = op.update
    c.resource_name = f"customers/{cid}/campaigns/{campaign_id}"
    c.status = status_enum
    op.update_mask.paths.append("status")

    result = _mutate("CampaignService", "mutate_campaigns", [op])
    if "error" in result:
        return result
    return {"updated": campaign_id, "type": "campaign", "status": status}


def update_ad_group_status(ad_group_id: str, status: str) -> dict:
    """Pause or enable an ad group. status: ENABLED or PAUSED."""
    err = _check_config()
    if err:
        return err

    cid = _customer_id()
    client = _build_client()
    try:
        status_enum = client.enums.AdGroupStatusEnum[status]
    except KeyError:
        return {"error": "INVALID_STATUS", "valid": ["ENABLED", "PAUSED", "REMOVED"]}

    op = client.get_type("AdGroupOperation")
    ag = op.update
    ag.resource_name = f"customers/{cid}/adGroups/{ad_group_id}"
    ag.status = status_enum
    op.update_mask.paths.append("status")

    result = _mutate("AdGroupService", "mutate_ad_groups", [op])
    if "error" in result:
        return result
    return {"updated": ad_group_id, "type": "ad_group", "status": status}


def update_keyword_bid(ad_group_id: str, criterion_id: str, bid_dollars: float) -> dict:
    """Update the max CPC bid for a positive keyword.

    criterion_id: from get_keywords — use the ad_group_criterion.criterion_id field.
    bid_dollars: new max CPC in account currency (e.g. 1.50 = $1.50).
    """
    err = _check_config()
    if err:
        return err

    cid = _customer_id()
    client = _build_client()

    op = client.get_type("AdGroupCriterionOperation")
    c = op.update
    c.resource_name = f"customers/{cid}/adGroupCriteria/{ad_group_id}~{criterion_id}"
    c.cpc_bid_micros = int(bid_dollars * 1_000_000)
    op.update_mask.paths.append("cpc_bid_micros")

    result = _mutate("AdGroupCriterionService", "mutate_ad_group_criteria", [op])
    if "error" in result:
        return result
    return {"updated": criterion_id, "ad_group_id": ad_group_id, "bid_dollars": bid_dollars}


def update_campaign_budget(campaign_id: str, daily_budget_dollars: float) -> dict:
    """Update the daily budget for a campaign.

    Fetches the campaign's budget resource name, then mutates it.
    """
    err = _check_config()
    if err:
        return err

    # Step 1: get budget resource name
    gaql = f"""
        SELECT campaign.campaign_budget
        FROM campaign
        WHERE campaign.id = {campaign_id}
        LIMIT 1
    """
    rows = _run_query(gaql)
    if isinstance(rows, dict) and "error" in rows:
        return rows
    if not rows:
        return {"error": "CAMPAIGN_NOT_FOUND", "campaign_id": campaign_id}

    budget_resource = rows[0].campaign.campaign_budget

    # Step 2: mutate budget
    cid = _customer_id()
    client = _build_client()
    op = client.get_type("CampaignBudgetOperation")
    b = op.update
    b.resource_name = budget_resource
    b.amount_micros = int(daily_budget_dollars * 1_000_000)
    op.update_mask.paths.append("amount_micros")

    result = _mutate("CampaignBudgetService", "mutate_campaign_budgets", [op])
    if "error" in result:
        return result
    return {"updated": campaign_id, "budget_resource": budget_resource,
            "daily_budget_dollars": daily_budget_dollars}


def create_campaign(
    name: str,
    channel_type: str,
    bidding_strategy: str,
    daily_budget_dollars: float,
    status: str = "PAUSED",
    target_cpa_dollars: float = None,
) -> dict:
    """Create a new Google Ads campaign.

    channel_type: SEARCH, DISPLAY, PERFORMANCE_MAX
    bidding_strategy: MAXIMIZE_CONVERSIONS, TARGET_CPA, MANUAL_CPC, MAXIMIZE_CONVERSION_VALUE
    status: ENABLED or PAUSED (default PAUSED — always review before activating)
    target_cpa_dollars: required when bidding_strategy is TARGET_CPA
    """
    err = _check_config()
    if err:
        return err

    cid = _customer_id()
    client = _build_client()

    try:
        channel_enum = client.enums.AdvertisingChannelTypeEnum[channel_type]
    except KeyError:
        return {"error": "INVALID_CHANNEL_TYPE", "valid": ["SEARCH", "DISPLAY", "PERFORMANCE_MAX"]}

    # Create budget operation (temp resource name -1)
    budget_op = client.get_type("CampaignBudgetOperation")
    budget = budget_op.create
    budget.resource_name = f"customers/{cid}/campaignBudgets/-1"
    budget.name = f"{name} Budget"
    budget.amount_micros = int(daily_budget_dollars * 1_000_000)
    budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD

    # Create campaign operation
    campaign_op = client.get_type("CampaignOperation")
    c = campaign_op.create
    c.resource_name = f"customers/{cid}/campaigns/-2"
    c.name = name
    c.advertising_channel_type = channel_enum
    c.status = client.enums.CampaignStatusEnum[status]
    c.campaign_budget = budget.resource_name

    strategy = bidding_strategy.upper()
    if strategy == "MAXIMIZE_CONVERSIONS":
        c.maximize_conversions.target_cpa_micros = 0
    elif strategy == "TARGET_CPA":
        if target_cpa_dollars is None:
            return {"error": "MISSING_PARAM", "hint": "target_cpa_dollars required for TARGET_CPA"}
        c.target_cpa.target_cpa_micros = int(target_cpa_dollars * 1_000_000)
    elif strategy == "MANUAL_CPC":
        c.manual_cpc.enhanced_cpc_enabled = False
    elif strategy == "MAXIMIZE_CONVERSION_VALUE":
        c.maximize_conversion_value.target_roas = 0
    else:
        return {"error": "INVALID_BIDDING_STRATEGY",
                "valid": ["MAXIMIZE_CONVERSIONS", "TARGET_CPA", "MANUAL_CPC", "MAXIMIZE_CONVERSION_VALUE"]}

    # Mutate budget and campaign together
    try:
        campaign_service = client.get_service("CampaignService")
        budget_service = client.get_service("CampaignBudgetService")

        b_result = budget_service.mutate_campaign_budgets(
            customer_id=cid, operations=[budget_op])
        budget_resource_name = b_result.results[0].resource_name
        c.campaign_budget = budget_resource_name

        c_result = campaign_service.mutate_campaigns(
            customer_id=cid, operations=[campaign_op])
        campaign_resource = c_result.results[0].resource_name
        campaign_id = campaign_resource.split("/")[-1]

        return {"campaign_id": campaign_id, "name": name, "channel_type": channel_type,
                "bidding_strategy": bidding_strategy, "daily_budget_dollars": daily_budget_dollars,
                "status": status, "resource_name": campaign_resource}

    except GoogleAdsException as ex:
        return {"error": "GOOGLE_ADS_API_ERROR",
                "details": [{"code": e.error_code.WhichOneof("error_code"), "message": e.message}
                             for e in ex.failure.errors]}
    except Exception as e:
        return {"error": "GOOGLE_REQUEST_FAILED", "message": str(e)}


def create_ad_group(
    campaign_id: str,
    name: str,
    cpc_bid_dollars: float = 1.0,
    status: str = "ENABLED",
) -> dict:
    """Create an ad group inside an existing campaign.

    cpc_bid_dollars: default max CPC bid for keywords in this ad group.
    """
    err = _check_config()
    if err:
        return err

    cid = _customer_id()
    client = _build_client()

    op = client.get_type("AdGroupOperation")
    ag = op.create
    ag.resource_name = f"customers/{cid}/adGroups/-1"
    ag.name = name
    ag.campaign = f"customers/{cid}/campaigns/{campaign_id}"
    ag.status = client.enums.AdGroupStatusEnum[status]
    ag.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
    ag.cpc_bid_micros = int(cpc_bid_dollars * 1_000_000)

    result = _mutate("AdGroupService", "mutate_ad_groups", [op])
    if "error" in result:
        return result
    resource_name = result["results"][0]
    ad_group_id = resource_name.split("/")[-1]
    return {"ad_group_id": ad_group_id, "name": name, "campaign_id": campaign_id,
            "cpc_bid_dollars": cpc_bid_dollars, "status": status}


def create_responsive_search_ad(
    ad_group_id: str,
    headlines: list,
    descriptions: list,
    final_url: str,
    path1: str = "",
    path2: str = "",
) -> dict:
    """Create a Responsive Search Ad (RSA) in an ad group.

    headlines: 3–15 strings, max 30 chars each
    descriptions: 2–4 strings, max 90 chars each
    final_url: landing page URL
    path1 / path2: optional display URL path fields (max 15 chars each)
    """
    err = _check_config()
    if err:
        return err

    if len(headlines) < 3:
        return {"error": "INVALID_INPUT", "hint": "Minimum 3 headlines required for RSA."}
    if len(descriptions) < 2:
        return {"error": "INVALID_INPUT", "hint": "Minimum 2 descriptions required for RSA."}

    cid = _customer_id()
    client = _build_client()

    op = client.get_type("AdGroupAdOperation")
    ad_group_ad = op.create
    ad_group_ad.ad_group = f"customers/{cid}/adGroups/{ad_group_id}"
    ad_group_ad.status = client.enums.AdGroupAdStatusEnum.PAUSED

    rsa = ad_group_ad.ad.responsive_search_ad
    for text in headlines:
        asset = client.get_type("AdTextAsset")
        asset.text = text[:30]
        rsa.headlines.append(asset)
    for text in descriptions:
        asset = client.get_type("AdTextAsset")
        asset.text = text[:90]
        rsa.descriptions.append(asset)
    if path1:
        rsa.path1 = path1[:15]
    if path2:
        rsa.path2 = path2[:15]

    ad_group_ad.ad.final_urls.append(final_url)

    result = _mutate("AdGroupAdService", "mutate_ad_group_ads", [op])
    if "error" in result:
        return result
    resource_name = result["results"][0]
    return {"resource_name": resource_name, "ad_group_id": ad_group_id,
            "final_url": final_url, "headlines_count": len(headlines),
            "descriptions_count": len(descriptions), "status": "PAUSED"}


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
