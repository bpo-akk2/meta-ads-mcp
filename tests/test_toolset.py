"""Tests for META_ADS_MCP_TOOLSET modes."""

import pytest
from mcp.server.fastmcp import FastMCP

from meta_ads_mcp.core.toolset import SUPPLEMENT_HIDDEN, apply_toolset, hidden_tools_for

# What a supplement-mode server must still expose next to the official Meta MCP.
SUPPLEMENT_VISIBLE = {
    "get_page_posts", "get_page_post", "boost_page_post",
    "search_interests", "get_interest_suggestions", "search_behaviors", "search_demographics",
    "search_geo_locations", "estimate_audience_size",
    "create_budget_schedule", "get_insights",
}


def _server(names):
    srv = FastMCP("t")
    for n in names:
        async def _f(x: str = "") -> str:  # noqa: ANN001
            return x
        _f.__name__ = n
        srv.add_tool(_f, name=n)
    return srv


@pytest.mark.asyncio
async def test_supplement_hides_only_duplicates_and_keeps_unique_tools():
    srv = _server(["create_campaign", "get_ads", "boost_page_post", "search_interests"])
    removed = await apply_toolset(srv, "supplement")
    assert removed == {"create_campaign", "get_ads"}
    assert {t.name for t in await srv.list_tools()} == {"boost_page_post", "search_interests"}


@pytest.mark.asyncio
async def test_full_mode_removes_nothing_and_is_the_default(monkeypatch):
    monkeypatch.delenv("META_ADS_MCP_TOOLSET", raising=False)
    srv = _server(["create_campaign", "boost_page_post"])
    assert await apply_toolset(srv) == set()
    assert len(await srv.list_tools()) == 2


@pytest.mark.asyncio
async def test_mode_comes_from_env(monkeypatch):
    monkeypatch.setenv("META_ADS_MCP_TOOLSET", "Supplement ")
    srv = _server(["create_campaign", "boost_page_post"])
    assert await apply_toolset(srv) == {"create_campaign"}


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError):
        hidden_tools_for("minimal")


@pytest.mark.asyncio
async def test_real_registry_supplement_view_matches_expected_tools():
    """Guard against a new upstream tool silently leaking into supplement mode unclassified."""
    from meta_ads_mcp.core import mcp_server
    registered = {t.name for t in await mcp_server.list_tools()}
    assert registered - SUPPLEMENT_HIDDEN == SUPPLEMENT_VISIBLE
