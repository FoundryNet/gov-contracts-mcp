"""gov-contracts-mcp — government contract search + federal procurement data for
autonomous agents.

A lean standalone MCP server (FastMCP, Streamable HTTP) exposing four tools over an
aggregated, deduplicated dataset of U.S. federal contracts — SAM.gov opportunities
and USASpending.gov / FPDS awards — that the contract_aggregator agent refreshes
every 6 hours:

  search_contracts        — filtered search of opportunities + awards   ($0.01)
  contract_detail         — full record for one solicitation number     (free)
  agency_spending         — live spend-by-NAICS + top awardees          ($0.01)
  trending_opportunities  — sectors by new-solicitation volume          ($0.01)

Paid tools take a daily free allowance, then x402 (USDC on Solana) per query. A
Bearer fnet_ key bypasses the paywall.

Transport: Streamable HTTP at /mcp (Railway + Smithery's hosted gateway). Legacy
SSE at /sse for older clients. Health: GET /health. Discovery: /.well-known/*.
"""
from __future__ import annotations

import inspect
import logging

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import core
import daily_curator
import identity
import payment_gate
import supa
import tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("gov.mcp")

if not supa.configured():
    logger.warning("SUPABASE_SERVICE_KEY not set — search/detail/trending will "
                   "return empty until it's configured.")

mcp = FastMCP("gov-contracts")

if payment_gate.is_active():
    logger.info(f"pay-per-query ARMED: {config.QUERY_PRICE_USDC} USDC → "
                f"{config.PAYMENT_RECIPIENT} after {config.FREE_TIER_DAILY}/day free "
                f"(rpc={config.PAYMENT_VERIFY_RPC})")
else:
    logger.info("pay-per-query INERT (X402_ENABLED off or PAYMENT_RECIPIENT unset) "
                "— all tools free")

tools.register_all(mcp)


# ── Health ──────────────────────────────────────────────────────────────────
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status":            "ok",
        "service":           "gov-contracts-mcp",
        "transport":         "streamable-http",
        "tools":             ["search_contracts", "contract_detail",
                              "agency_spending", "trending_opportunities",
                              "daily_brief", "mint_info"],
        "dataset":           "supabase:gov_contracts" if supa.configured() else "unconfigured",
        "x402_enabled":      config.X402_ENABLED,
        "query_payment":     "armed" if payment_gate.is_active() else "free",
        "query_price_usdc":  config.QUERY_PRICE_USDC,
        "free_tier_daily":   config.FREE_TIER_DAILY,
        "payment_recipient": config.PAYMENT_RECIPIENT,
        "payment_ledger":    "supabase" if supa.configured() else "in_memory",
    })


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ── REST surface (same core, so the paywall never drifts) ────────────────────
_ERR_STATUS = {"bad_request": 400, "not_configured": 503, "not_found": 404,
               "payment_required": 402}


def _resp(d: dict) -> JSONResponse:
    if "error" not in d:
        return JSONResponse(d, status_code=200)
    err = str(d.get("error") or "")
    if err in _ERR_STATUS:
        code = _ERR_STATUS[err]
    elif err.startswith("http_") and err[5:].isdigit():
        code = int(err[5:])
    elif err in ("network", "non_json_response", "unreachable"):
        code = 502
    else:
        code = 400
    return JSONResponse(d, status_code=code)


async def _json_body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def _rest_agent_key(request: Request, body: dict) -> str:
    return identity.resolve_agent_key(body.get("agent_id"), request=request)


@mcp.custom_route("/v1/search", methods=["POST"])
async def rest_search(request: Request) -> JSONResponse:
    b = await _json_body(request)
    filters = {k: b.get(k) for k in (
        "keyword", "agency", "naics", "state", "min_value", "max_value",
        "status", "posted_after", "limit")}
    return _resp(await core.do_search(
        filters, agent_key=_rest_agent_key(request, b),
        payment_tx=b.get("payment_tx"), api_key=identity.bearer(request)))


@mcp.custom_route("/v1/detail", methods=["POST"])
async def rest_detail(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_detail(b.get("solicitation_number", "")))


@mcp.custom_route("/v1/agency-spending", methods=["POST"])
async def rest_spending(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_spending(
        b.get("agency", ""), b.get("fiscal_year"),
        agent_key=_rest_agent_key(request, b),
        payment_tx=b.get("payment_tx"), api_key=identity.bearer(request)))


@mcp.custom_route("/v1/trending", methods=["POST"])
async def rest_trending(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_trending(
        b.get("naics"), b.get("days", 30),
        agent_key=_rest_agent_key(request, b),
        payment_tx=b.get("payment_tx"), api_key=identity.bearer(request)))


# ── Discovery ────────────────────────────────────────────────────────────────
_AGENT_CARD = {
    "name": "Government Contracts Intelligence MCP",
    "description": (
        "Search U.S. federal government contracts, solicitations, and awards by "
        "agency, value, NAICS code, or keyword — live from SAM.gov and USASpending, "
        "with response deadlines."
    ),
    "url": "https://gov-contracts-mcp-production.up.railway.app/mcp",
    "version": "1.0.0",
    "capabilities": {
        "tools": [
            "search_contracts", "contract_detail", "agency_spending",
            "trending_opportunities", "daily_brief", "mint_info",
        ],
    },
    "provider": {"name": "FoundryNet", "url": "https://foundrynet.io"},
    "network": "FoundryNet Data Network",
    "attestation": {
        "protocol": "MINT Protocol",
        "endpoint": "https://mint-mcp-production.up.railway.app/mcp",
        "verified_outputs": True, "live_feed": "https://mint.foundrynet.io/feed", "feed_api": "https://mint-mcp-production.up.railway.app/v1/feed",
    },
    "see_also": config.SISTER_SERVERS,
    "x402": {"supported": True, "currency": "USDC", "network": "solana"},
    "contact": "hello@foundrynet.io",
}


@mcp.custom_route("/.well-known/agent-card.json", methods=["GET"])
async def agent_card(request: Request) -> JSONResponse:
    return JSONResponse(_AGENT_CARD, headers={"Cache-Control": "public, max-age=300"})


@mcp.custom_route("/.well-known/mcp", methods=["GET"])
async def mcp_endpoints(request: Request) -> JSONResponse:
    return JSONResponse(
        {"endpoints": [{"url": config.PUBLIC_MCP_URL, "transport": "streamable-http",
                        "name": "Government Contracts MCP"}]},
        headers={"Cache-Control": "public, max-age=300"})


async def _live_tools() -> list:
    res = mcp.list_tools()
    if inspect.iscoroutine(res):
        res = await res
    out = []
    for t in res:
        out.append({
            "name": t.name,
            "description": (getattr(t, "description", "") or "").strip(),
            "inputSchema": getattr(t, "parameters", None) or {"type": "object"},
        })
    return out


@mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def server_card(request: Request) -> JSONResponse:
    live = await _live_tools()
    return JSONResponse(
        {
            "serverInfo": {"name": "Government Contracts MCP", "version": "1.0.0"},
            "authentication": {
                "type": "http", "scheme": "bearer",
                "description": ("contract_detail is free; the other tools give a daily "
                                "free allowance then take an fnet_ Bearer key OR an "
                                "x402 USDC payment."),
            },
            "tools": live,
            "version": "1.0",
            "name": "Government Contracts MCP",
            "tagline": "Government contract search + federal procurement data for agents.",
            "description": (
                "Search U.S. federal government contracts — SAM.gov opportunities and "
                "USASpending/FPDS awards — from one aggregated dataset. Look up "
                "contract detail, agency spending by NAICS, and trending solicitation "
                "sectors. Free tier, then 1¢ USDC per query (x402)."
            ),
            "serverUrl": config.PUBLIC_MCP_URL,
            "transport": "streamable-http",
            "tools_count": len(live),
            "categories": ["government", "data", "search", "finance", "procurement"],
            "pricing": {
                "model": "metered",
                "free_tier": f"{config.FREE_TIER_DAILY} queries/day per agent + free detail lookups",
                "paid_from": f"{config.QUERY_PRICE_USDC} USDC per query (x402)",
            },
        },
        headers={"Cache-Control": "public, max-age=300"})


# ── Entrypoint ───────────────────────────────────────────────────────────────
_FREE_TOOL_NAMES = {"mint_info", "macro_dashboard", "cve_detail", "detail",
                    "domain_age", "convert", "rates", "market_overview", "price",
                    "quote", "batch_quote", "sector_performance"}


@mcp.custom_route("/.well-known/mcp.json", methods=["GET"])
async def wellknown_mcp_json(request: Request) -> JSONResponse:
    """Machine-discovery card (emerging standard) for AI clients/crawlers."""
    live = await _live_tools()
    names = [t["name"] for t in live]
    return JSONResponse({
        "name": _AGENT_CARD["name"],
        "description": _AGENT_CARD["description"],
        "url": config.PUBLIC_MCP_URL,
        "transport": ["streamable-http"],
        "tools": names,
        "pricing": {"model": "per-query", "free_tier": True,
                    "paid_tools": [n for n in names if n not in _FREE_TOOL_NAMES]},
        "attestation": {"enabled": True, "protocol": "MINT Protocol",
                        "feed": "https://mint.foundrynet.io/feed"},
        "network": {"name": "FoundryNet Data Network", "servers": 17,
                    "homepage": "https://foundrynet.io"},
    }, headers={"Cache-Control": "public, max-age=300"})


def build_dual_app():
    """Serve Streamable HTTP at /mcp (primary, + all custom routes) and graft the
    legacy SSE transport routes (/sse, /messages) on so old configs keep working."""
    import asyncio
    import contextlib
    main_app = mcp.http_app(transport="http", path="/mcp")
    sse_app = mcp.http_app(transport="sse", path="/sse")
    for r in sse_app.routes:
        if getattr(r, "path", None) in ("/sse", "/messages"):
            main_app.router.routes.append(r)
    main_life, sse_life = main_app.router.lifespan_context, sse_app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _dual_lifespan(app):
        async with main_life(app):
            async with sse_life(app):
                brief_task = asyncio.create_task(daily_curator.curator_loop())
                try:
                    yield
                finally:
                    brief_task.cancel()
                    with contextlib.suppress(Exception):
                        await brief_task
    main_app.router.lifespan_context = _dual_lifespan
    return main_app


if __name__ == "__main__":
    import uvicorn
    logger.info(
        f"gov-contracts-mcp starting on 0.0.0.0:{config.PORT} "
        f"(dataset={'supabase' if supa.configured() else 'unconfigured'}, "
        f"x402={config.X402_ENABLED}) — /mcp (streamable-http) + /sse (legacy)")
    uvicorn.run(build_dual_app(), host="0.0.0.0", port=config.PORT, log_level="warning")
