"""Toolset modes: expose the full tool list, or only what the official Meta Ads MCP lacks.

META_ADS_MCP_TOOLSET=full        (default) every registered tool
META_ADS_MCP_TOOLSET=supplement  hide tools that duplicate the official Meta connector
                                 (campaign/ad set/ad/creative CRUD, account & page lookups,
                                 image/video helpers, Ads Library, Pipeboard-only tools)

"supplement" is for running this server next to the official connector: writes that the
official one can do stay on the user's own Meta login (per-person audit trail), and this
server only adds what it cannot — Page post boosting, targeting research, budget schedules,
raw insights. The hidden functions stay importable, so boost_page_post keeps chaining
create_campaign → create_adset → create_ad_creative → create_ad internally.
"""

import logging
import os
from typing import Iterable, Set

logger = logging.getLogger(__name__)

ENV_VAR = "META_ADS_MCP_TOOLSET"
MODES = ("full", "supplement")

# Everything with a 1:1 counterpart in the official Meta Ads MCP (ads_* tools).
SUPPLEMENT_HIDDEN: Set[str] = {
    # accounts & pages
    "get_ad_accounts", "get_account_info", "get_account_pages", "search_pages_by_name",
    # campaigns
    "get_campaigns", "get_campaign_details", "create_campaign", "update_campaign",
    # ad sets
    "get_adsets", "get_adset_details", "create_adset", "update_adset",
    # ads
    "get_ads", "get_ad_details", "create_ad", "update_ad",
    # creatives & media
    "get_ad_creatives", "get_creative_details", "create_ad_creative", "update_ad_creative",
    "upload_ad_image", "get_ad_image", "get_ad_video", "get_image_by_hash", "compute_image_crops",
    # Ads Library
    "search_ads_archive",
    # Pipeboard / OpenAI deep-research plumbing — irrelevant behind our own auth proxy
    "get_login_link", "search", "fetch", "duplicate_campaign", "duplicate_adset", "duplicate_ad",
    "duplicate_creative", "generate_report",
}


def hidden_tools_for(mode: str) -> Set[str]:
    if mode not in MODES:
        raise ValueError(f"{ENV_VAR} must be one of {MODES}, got {mode!r}")
    return set(SUPPLEMENT_HIDDEN) if mode == "supplement" else set()


async def apply_toolset(mcp_server, mode: str = None) -> Set[str]:
    """Remove hidden tools from the server's registry. Returns the names actually removed."""
    mode = (mode or os.environ.get(ENV_VAR) or "full").strip().lower()
    hidden = hidden_tools_for(mode)
    if not hidden:
        return set()
    registered = {t.name for t in await mcp_server.list_tools()}
    removed = set()
    for name in sorted(hidden & registered):
        mcp_server.remove_tool(name)
        removed.add(name)
    logger.info(f"Toolset '{mode}': hid {len(removed)} tools, {len(registered) - len(removed)} exposed")
    return removed


def apply_toolset_sync(mcp_server, mode: str = None) -> Set[str]:
    """Blocking wrapper for the startup path (no event loop running yet)."""
    import asyncio
    return asyncio.run(apply_toolset(mcp_server, mode))
