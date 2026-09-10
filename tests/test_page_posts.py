"""Tests for Facebook Page post tools: get_page_posts, get_page_post, boost_page_post."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from meta_ads_mcp.core.pages import get_page_posts, get_page_post, boost_page_post

PAGE = "484197734962371"
POST = f"{PAGE}_931045965194925"
TOKEN = "test_token"


def _load(raw: str) -> dict:
    """Unwrap the {"data": "<json>"} envelope @meta_api_tool puts around error responses."""
    data = json.loads(raw)
    if set(data) == {"data"} and isinstance(data["data"], str):
        data = json.loads(data["data"])
    return data


def _post(post_id=POST, promotable=None, eligible=True):
    data = {
        "id": post_id,
        "message": "Hello",
        "created_time": "2026-09-01T10:00:00+0000",
        "permalink_url": f"https://www.facebook.com/{PAGE}/posts/{post_id.split('_')[-1]}",
        "is_eligible_for_promotion": eligible,
    }
    if promotable:
        data["promotable_id"] = promotable
    return data


@pytest.mark.asyncio
async def test_get_page_posts_adds_object_story_id_and_filters():
    with patch("meta_ads_mcp.core.pages.make_api_request", new_callable=AsyncMock) as api:
        api.return_value = {
            "data": [
                _post(),
                _post(post_id=f"{PAGE}_111", promotable=f"{PAGE}_999", eligible=False),
            ],
            "paging": {"cursors": {"after": "abc"}},
        }

        result = _load(await get_page_posts(page_id=PAGE, limit=5, since="2026-08-01", access_token=TOKEN))

        endpoint, token, params = api.call_args[0]
        assert endpoint == f"{PAGE}/posts"
        assert token == TOKEN
        assert params["limit"] == 5 and params["since"] == "2026-08-01"
        assert "promotable_id" in params["fields"] and "is_eligible_for_promotion" in params["fields"]

        assert result["count"] == 2
        assert result["data"][0]["object_story_id"] == POST
        # promotable_id wins over id and gets flagged
        assert result["data"][1]["object_story_id"] == f"{PAGE}_999"
        assert "note" in result["data"][1]
        assert result["paging"]["cursors"]["after"] == "abc"

        filtered = _load(await get_page_posts(page_id=PAGE, only_promotable=True, access_token=TOKEN))
        assert filtered["count"] == 1 and filtered["data"][0]["id"] == POST


@pytest.mark.asyncio
async def test_get_page_posts_error_carries_permission_hint():
    with patch("meta_ads_mcp.core.pages.make_api_request", new_callable=AsyncMock) as api:
        api.return_value = {"error": {"message": "(#10) Permission denied", "code": 10}}
        result = _load(await get_page_posts(page_id=PAGE, access_token=TOKEN))
        assert result["error"]["code"] == 10
        assert "pages_read_engagement" in result["hint"]


@pytest.mark.asyncio
async def test_get_page_post_builds_full_id_from_bare_id():
    with patch("meta_ads_mcp.core.pages.make_api_request", new_callable=AsyncMock) as api:
        api.return_value = _post()
        result = _load(await get_page_post(post_id="931045965194925", page_id=PAGE, access_token=TOKEN))
        assert api.call_args[0][0] == POST
        assert result["object_story_id"] == POST

        # already-qualified id is passed through untouched
        await get_page_post(post_id=POST, access_token=TOKEN)
        assert api.call_args[0][0] == POST


@pytest.mark.asyncio
async def test_boost_page_post_happy_path_chains_all_steps_paused():
    with patch("meta_ads_mcp.core.pages.make_api_request", new_callable=AsyncMock) as api, \
         patch("meta_ads_mcp.core.pages.create_campaign", new_callable=AsyncMock) as campaign, \
         patch("meta_ads_mcp.core.pages.create_adset", new_callable=AsyncMock) as adset, \
         patch("meta_ads_mcp.core.pages.create_ad_creative", new_callable=AsyncMock) as creative, \
         patch("meta_ads_mcp.core.pages.create_ad", new_callable=AsyncMock) as ad:
        api.return_value = _post(promotable=f"{PAGE}_777")
        campaign.return_value = json.dumps({"id": "c1"})
        adset.return_value = json.dumps({"id": "s1"})
        creative.return_value = json.dumps({"id": "cr1", "name": "x"})
        ad.return_value = json.dumps({"id": "a1"})

        result = _load(await boost_page_post(
            account_id="1670847109878128", post_id=POST, daily_budget=2000,
            dsa_beneficiary="K2 Precise", dsa_payor="K2 Precise", access_token=TOKEN,
        ))

        assert result == {
            "account_id": "act_1670847109878128",
            "requested_status": "PAUSED",
            "post": {
                "id": POST, "object_story_id": f"{PAGE}_777",
                "permalink_url": f"https://www.facebook.com/{PAGE}/posts/931045965194925",
                "is_eligible_for_promotion": True, "created_time": "2026-09-01T10:00:00+0000",
            },
            "campaign_created": True, "campaign_id": "c1", "adset_id": "s1",
            "creative_id": "cr1", "ad_id": "a1", "status": "PAUSED",
            "next_step": result["next_step"],
        }

        # campaign: engagement objective, budget pushed down to the ad set, paused
        ckw = campaign.call_args.kwargs
        assert ckw["objective"] == "OUTCOME_ENGAGEMENT" and ckw["use_adset_level_budgets"] is True
        assert ckw["status"] == "PAUSED" and ckw["access_token"] == TOKEN

        skw = adset.call_args.kwargs
        assert skw["campaign_id"] == "c1" and skw["daily_budget"] == 2000
        assert skw["optimization_goal"] == "POST_ENGAGEMENT" and skw["destination_type"] == "ON_POST"
        assert skw["targeting"]["geo_locations"]["countries"] == ["PL"]
        assert skw["dsa_beneficiary"] == "K2 Precise"

        # creative references promotable_id, not the raw post id
        assert creative.call_args.kwargs["object_story_id"] == f"{PAGE}_777"
        assert "image_hash" not in creative.call_args.kwargs

        akw = ad.call_args.kwargs
        assert akw["adset_id"] == "s1" and akw["creative_id"] == "cr1" and akw["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_boost_page_post_reuses_campaign_and_reports_partial_failure():
    with patch("meta_ads_mcp.core.pages.make_api_request", new_callable=AsyncMock) as api, \
         patch("meta_ads_mcp.core.pages.create_campaign", new_callable=AsyncMock) as campaign, \
         patch("meta_ads_mcp.core.pages.create_adset", new_callable=AsyncMock) as adset, \
         patch("meta_ads_mcp.core.pages.create_ad_creative", new_callable=AsyncMock) as creative, \
         patch("meta_ads_mcp.core.pages.create_ad", new_callable=AsyncMock) as ad:
        api.return_value = _post()
        adset.return_value = json.dumps({"id": "s1"})
        creative.return_value = json.dumps({"error": "Failed to create ad creative", "details": "Post not owned by ad's Page"})

        result = _load(await boost_page_post(
            account_id="act_1", post_id=POST, campaign_id="existing", daily_budget=1000, access_token=TOKEN,
        ))

        campaign.assert_not_called()
        ad.assert_not_called()
        assert result["campaign_id"] == "existing" and "campaign_created" not in result
        assert result["adset_id"] == "s1"          # created before the failure — reported, not hidden
        assert "creative_id" not in result
        assert result["error"]["step"] == "create_ad_creative"
        assert result["error"]["message"] == "Failed to create ad creative"


@pytest.mark.asyncio
async def test_boost_page_post_refuses_ineligible_post_unless_forced():
    with patch("meta_ads_mcp.core.pages.make_api_request", new_callable=AsyncMock) as api, \
         patch("meta_ads_mcp.core.pages.create_campaign", new_callable=AsyncMock) as campaign, \
         patch("meta_ads_mcp.core.pages.create_adset", new_callable=AsyncMock) as adset, \
         patch("meta_ads_mcp.core.pages.create_ad_creative", new_callable=AsyncMock) as creative, \
         patch("meta_ads_mcp.core.pages.create_ad", new_callable=AsyncMock) as ad:
        api.return_value = _post(eligible=False)

        result = _load(await boost_page_post(account_id="act_1", post_id=POST, daily_budget=1000, access_token=TOKEN))
        assert result["error"]["step"] == "eligibility"
        campaign.assert_not_called()

        for mock, rid in ((campaign, "c1"), (adset, "s1"), (creative, "cr1"), (ad, "a1")):
            mock.return_value = json.dumps({"id": rid})
        forced = _load(await boost_page_post(account_id="act_1", post_id=POST, daily_budget=1000, force=True, access_token=TOKEN))
        assert forced["ad_id"] == "a1"


@pytest.mark.asyncio
async def test_boost_page_post_validates_budget_before_any_call():
    with patch("meta_ads_mcp.core.pages.make_api_request", new_callable=AsyncMock) as api:
        no_budget = _load(await boost_page_post(account_id="act_1", post_id=POST, access_token=TOKEN))
        assert "daily_budget or lifetime_budget" in no_budget["error"]

        no_end = _load(await boost_page_post(account_id="act_1", post_id=POST, lifetime_budget=5000, access_token=TOKEN))
        assert "end_time" in no_end["error"]

        api.assert_not_called()
