"""Facebook Page tools: browse organic posts and boost them as ads.

The Marketing API has no separate "boost" engine — promoting an organic post is
an ordinary ad whose creative points at the post via ``object_story_id``. These
tools close the gap between the Page (where posts live) and the ad account
(where the boost is built): list posts with their ``promotable_id``, and run the
whole campaign → ad set → creative → ad chain in one call.

Page endpoints require the token to have a role on the Page (system user with
the Page assigned, scopes ``pages_show_list`` + ``pages_read_engagement``;
``pages_manage_ads`` for boosting).
"""

import json
from typing import Any, Dict, List, Optional, Union

from .api import meta_api_tool, make_api_request, ensure_act_prefix
from .server import mcp_server
from .campaigns import create_campaign
from .adsets import create_adset
from .ads import create_ad_creative, create_ad

# promotable_id is what the ad creative must reference — for shared posts and
# some New-Page-Experience pages it differs from the post's own id.
PAGE_POST_FIELDS = (
    "id,message,created_time,permalink_url,status_type,full_picture,is_published,"
    "is_eligible_for_promotion,promotable_id,attachments{media_type,type,url}"
)


def _has_error(data: Any) -> bool:
    return isinstance(data, dict) and "error" in data


def _parse_tool_result(raw: str) -> Dict[str, Any]:
    """Tools return JSON strings; unwrap them into dicts for chaining."""
    try:
        data = json.loads(raw)
        # meta_api_tool wraps non-JSON strings as {"data": "..."}; unwrap when that string is itself JSON
        if isinstance(data, dict) and set(data) == {"data"} and isinstance(data["data"], str):
            data = json.loads(data["data"])
    except (TypeError, ValueError):
        return {"error": {"message": "Unparseable tool response", "raw": raw}}
    return data if isinstance(data, dict) else {"data": data}


def _step_error(step: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a failed step into {'step': ..., ...error fields}."""
    err = data.get("error")
    if not isinstance(err, dict):
        err = {"message": err or "no id returned"}
    return {"step": step, **err}


def _post_summary(post: Dict[str, Any]) -> Dict[str, Any]:
    """Add the fields an agent needs to boost the post without guessing."""
    post_id = post.get("id", "")
    object_story_id = post.get("promotable_id") or post_id
    summary = dict(post)
    summary["object_story_id"] = object_story_id
    if post.get("promotable_id") and post["promotable_id"] != post_id:
        summary["note"] = "promotable_id differs from id — use object_story_id (= promotable_id) for the creative."
    return summary


def _normalize_post_id(post_id: Union[str, int], page_id: Optional[Union[str, int]]) -> str:
    """Accept '{page_id}_{post_id}', a bare post id + page_id, or a bare post id."""
    post_id = str(post_id).strip()
    if "_" in post_id or not page_id:
        return post_id
    return f"{page_id}_{post_id}"


@mcp_server.tool()
@meta_api_tool
async def get_page_posts(
    page_id: Union[str, int],
    limit: int = 25,
    since: Optional[str] = None,
    until: Optional[str] = None,
    only_promotable: bool = False,
    access_token: Optional[str] = None,
) -> str:
    """
    List organic posts published on a Facebook Page, with the ids needed to boost them.

    Each post carries `object_story_id` — pass it to create_ad_creative (or boost_page_post)
    to promote that post. `is_eligible_for_promotion` tells you up front whether Meta will
    accept the post as an ad.

    Args:
        page_id: Facebook Page ID (use get_account_pages to find it)
        limit: Maximum number of posts to return (default: 25)
        since: Only posts created after this date (YYYY-MM-DD or unix timestamp)
        until: Only posts created before this date (YYYY-MM-DD or unix timestamp)
        only_promotable: Return only posts Meta reports as eligible for promotion
        access_token: Meta API access token (optional - will use cached token if not provided)

    Returns:
        JSON with `data` (posts incl. object_story_id, promotable_id, is_eligible_for_promotion,
        permalink_url, message excerpt) and `paging`.
    """
    if not page_id:
        return json.dumps({"error": "No page ID provided"}, indent=2)

    params: Dict[str, Any] = {"fields": PAGE_POST_FIELDS, "limit": limit}
    if since:
        params["since"] = since
    if until:
        params["until"] = until

    data = await make_api_request(f"{page_id}/posts", access_token, params)
    if _has_error(data):
        data["hint"] = (
            "Page posts need a token with a role on this Page: assign the Page to the system user "
            "in Business Settings and generate the token with pages_show_list + pages_read_engagement."
        )
        return json.dumps(data, indent=2)

    posts = [_post_summary(p) for p in data.get("data", [])]
    if only_promotable:
        posts = [p for p in posts if p.get("is_eligible_for_promotion")]

    return json.dumps({
        "page_id": str(page_id),
        "count": len(posts),
        "data": posts,
        "paging": data.get("paging", {}),
    }, indent=2)


@mcp_server.tool()
@meta_api_tool
async def get_page_post(
    post_id: Union[str, int],
    page_id: Optional[Union[str, int]] = None,
    access_token: Optional[str] = None,
) -> str:
    """
    Get one Facebook Page post with its promotable_id and eligibility — the check to run
    before boosting when a post URL or bare post id is all you have.

    Args:
        post_id: Post ID — either '{page_id}_{post_id}' or the bare post id from the URL
                 (then also pass page_id)
        page_id: Facebook Page ID; required when post_id is the bare id from a post URL
        access_token: Meta API access token (optional - will use cached token if not provided)

    Returns:
        JSON with the post fields plus `object_story_id` to use in create_ad_creative.
    """
    if not post_id:
        return json.dumps({"error": "No post ID provided"}, indent=2)

    full_id = _normalize_post_id(post_id, page_id)
    data = await make_api_request(full_id, access_token, {"fields": PAGE_POST_FIELDS})
    if _has_error(data):
        if "_" not in full_id:
            data["hint"] = "Pass page_id as well, or use the '{page_id}_{post_id}' form."
        return json.dumps(data, indent=2)

    return json.dumps(_post_summary(data), indent=2)


@mcp_server.tool()
@meta_api_tool
async def boost_page_post(
    account_id: str,
    post_id: Union[str, int],
    page_id: Optional[Union[str, int]] = None,
    campaign_id: Optional[str] = None,
    campaign_name: Optional[str] = None,
    adset_name: Optional[str] = None,
    ad_name: Optional[str] = None,
    daily_budget: Optional[int] = None,
    lifetime_budget: Optional[int] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    targeting: Optional[Dict[str, Any]] = None,
    objective: str = "OUTCOME_ENGAGEMENT",
    optimization_goal: str = "POST_ENGAGEMENT",
    billing_event: str = "IMPRESSIONS",
    destination_type: Optional[str] = "ON_POST",
    bid_strategy: Optional[str] = None,
    bid_amount: Optional[int] = None,
    dsa_beneficiary: Optional[str] = None,
    dsa_payor: Optional[str] = None,
    special_ad_categories: Optional[List[str]] = None,
    status: str = "PAUSED",
    force: bool = False,
    access_token: Optional[str] = None,
) -> str:
    """
    Boost an existing organic Facebook Page post: builds campaign (optional) → ad set →
    creative (object_story_id) → ad in one call. Everything is created PAUSED unless
    `status` says otherwise — review in Ads Manager, then activate.

    Fails fast (before spending any API writes) if the post is not eligible for promotion,
    and reports every id created so far if a later step fails, so nothing is left orphaned
    without you knowing.

    Args:
        account_id: Meta Ads account ID (format: act_XXXXXXXXX)
        post_id: Post to boost — '{page_id}_{post_id}' or bare post id (then pass page_id)
        page_id: Facebook Page ID that owns the post (required with a bare post_id)
        campaign_id: Reuse an existing campaign; when omitted a new one is created
        campaign_name: Name for the new campaign (default: 'Boost <post id>')
        adset_name: Ad set name (default: 'Boost <post id> - adset')
        ad_name: Ad name (default: 'Boost <post id>')
        daily_budget: Ad set daily budget in account currency cents (one of daily/lifetime required)
        lifetime_budget: Ad set lifetime budget in cents (requires end_time)
        start_time: Ad set start (ISO 8601); defaults to now
        end_time: Ad set end (ISO 8601); required with lifetime_budget
        targeting: Targeting spec (geo_locations, age_min/max, interests...). Use search_geo_locations /
                   search_interests for ids. Defaults to Poland, 18-65 when omitted.
        objective: Campaign objective for the new campaign (default: OUTCOME_ENGAGEMENT)
        optimization_goal: Ad set optimization goal (default: POST_ENGAGEMENT; also REACH, LINK_CLICKS...)
        billing_event: Ad set billing event (default: IMPRESSIONS)
        destination_type: Ad set destination (default: ON_POST; set None to omit)
        bid_strategy: Ad set bid strategy (default: campaign's / LOWEST_COST_WITHOUT_CAP)
        bid_amount: Bid cap in cents when bid_strategy needs one
        dsa_beneficiary: DSA beneficiary (EU accounts — usually the client's legal name)
        dsa_payor: DSA payor (EU accounts — usually the agency)
        special_ad_categories: e.g. ['NONE'] or ['HOUSING'] for the new campaign
        status: Status for every created object (default: PAUSED)
        force: Boost even if Meta reports is_eligible_for_promotion=false
        access_token: Meta API access token (optional - will use cached token if not provided)

    Returns:
        JSON with post, campaign_id, adset_id, creative_id, ad_id and status — or a partial
        result plus `error` naming the step that failed.
    """
    if not account_id:
        return json.dumps({"error": "No account ID provided"}, indent=2)
    if not post_id:
        return json.dumps({"error": "No post ID provided"}, indent=2)
    if not daily_budget and not lifetime_budget:
        return json.dumps({"error": "Provide daily_budget or lifetime_budget (in account currency cents)"}, indent=2)
    if lifetime_budget and not end_time:
        return json.dumps({"error": "lifetime_budget requires end_time"}, indent=2)

    account_id = ensure_act_prefix(account_id)
    full_id = _normalize_post_id(post_id, page_id)
    result: Dict[str, Any] = {"account_id": account_id, "requested_status": status}

    # 1. Resolve the post — the creative must reference promotable_id, not necessarily the post id.
    post = await make_api_request(full_id, access_token, {"fields": PAGE_POST_FIELDS})
    if _has_error(post):
        result["error"] = _step_error("get_post", post)
        return json.dumps(result, indent=2)
    post = _post_summary(post)
    result["post"] = {k: post.get(k) for k in ("id", "object_story_id", "permalink_url", "is_eligible_for_promotion", "created_time")}
    if post.get("is_eligible_for_promotion") is False and not force:
        result["error"] = {
            "step": "eligibility",
            "message": "Meta reports this post is not eligible for promotion (is_eligible_for_promotion=false). "
                       "Pass force=true to try anyway.",
        }
        return json.dumps(result, indent=2)

    object_story_id = post["object_story_id"]
    short = object_story_id.split("_")[-1]
    label = f"Boost {short}"

    # 2. Campaign — reuse or create. Budget lives on the ad set so the campaign holds none.
    if not campaign_id:
        campaign = _parse_tool_result(await create_campaign(
            account_id=account_id,
            name=campaign_name or label,
            objective=objective,
            status=status,
            special_ad_categories=special_ad_categories,
            use_adset_level_budgets=True,
            access_token=access_token,
        ))
        if _has_error(campaign) or not campaign.get("id"):
            result["error"] = _step_error("create_campaign", campaign)
            return json.dumps(result, indent=2)
        campaign_id = campaign["id"]
        result["campaign_created"] = True
    result["campaign_id"] = campaign_id

    # 3. Ad set
    adset = _parse_tool_result(await create_adset(
        account_id=account_id,
        campaign_id=campaign_id,
        name=adset_name or f"{label} - adset",
        optimization_goal=optimization_goal,
        billing_event=billing_event,
        status=status,
        daily_budget=daily_budget,
        lifetime_budget=lifetime_budget,
        targeting=targeting or {"geo_locations": {"countries": ["PL"]}, "age_min": 18, "age_max": 65},
        bid_strategy=bid_strategy,
        bid_amount=bid_amount,
        start_time=start_time,
        end_time=end_time,
        dsa_beneficiary=dsa_beneficiary,
        dsa_payor=dsa_payor,
        destination_type=destination_type,
        access_token=access_token,
    ))
    if _has_error(adset) or not adset.get("id"):
        result["error"] = _step_error("create_adset", adset)
        return json.dumps(result, indent=2)
    result["adset_id"] = adset["id"]

    # 4. Creative from the existing post
    creative = _parse_tool_result(await create_ad_creative(
        account_id=account_id,
        object_story_id=object_story_id,
        name=f"{label} - creative",
        access_token=access_token,
    ))
    if _has_error(creative) or not creative.get("id"):
        result["error"] = _step_error("create_ad_creative", creative)
        return json.dumps(result, indent=2)
    result["creative_id"] = creative["id"]

    # 5. Ad
    ad = _parse_tool_result(await create_ad(
        account_id=account_id,
        name=ad_name or label,
        adset_id=adset["id"],
        creative_id=creative["id"],
        status=status,
        access_token=access_token,
    ))
    if _has_error(ad) or not ad.get("id"):
        result["error"] = _step_error("create_ad", ad)
        return json.dumps(result, indent=2)
    result["ad_id"] = ad["id"]
    result["status"] = status
    if status == "PAUSED":
        result["next_step"] = "Review in Ads Manager, then activate the ad set/ad (update_adset / update_ad with status=ACTIVE)."
    return json.dumps(result, indent=2)
