"""gov-contracts-mcp tools. Each module exposes a `register(mcp)` that attaches its
@mcp.tool to the shared FastMCP instance — one tool per file.

Four tools over federal contract data:
  search_contracts       — filtered search of opportunities + awards   ($0.01)
  contract_detail        — full record for one solicitation number     (free)
  agency_spending        — live spend-by-NAICS + top awardees          ($0.01)
  trending_opportunities — sectors by new-solicitation volume          ($0.01)
  daily_brief            — curated daily federal-procurement brief      ($10)
  mint_info              — FoundryNet Data Network + MINT cross-promo    (free)
"""
from . import search as search_tool
from . import detail as detail_tool
from . import spending as spending_tool
from . import trending as trending_tool
from . import daily_brief as daily_brief_tool
from . import mint as mint_tool


def register_all(mcp) -> None:
    search_tool.register(mcp)
    detail_tool.register(mcp)
    spending_tool.register(mcp)
    trending_tool.register(mcp)
    daily_brief_tool.register(mcp)
    mint_tool.register(mcp)
