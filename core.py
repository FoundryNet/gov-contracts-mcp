"""Shared business logic behind BOTH the MCP tools and the REST routes, so the
paywall + query behaviour never drift between surfaces.

Three paid operations (search / agency_spending / trending) run the x402 gate via
payment_gate.precheck before querying; contract_detail is free. Each returns plain
dicts/lists — the MCP tool returns them directly, the REST route maps them to
JSON (402 on a payment_required body).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import config
import daily_curator
import mint_integration
import payment_gate
import supa
import usaspending


def _billing(decision: dict) -> dict:
    g = decision.get("gate")
    if g == "free":
        cap = decision.get("cap")
        cnt = decision.get("count")
        return {"tier": "free", "used_today": cnt, "daily_free": cap,
                "remaining_today": (cap - cnt) if (cap is not None and cnt is not None) else None}
    if g == "paid":
        return {"tier": "paid", "charged_usdc": decision.get("amount_usdc")}
    if g == "api_key":
        return {"tier": "api_key", "note": "billed to your Forge account"}
    return {"tier": "free", "note": "gating inert"}


async def do_search(filters: dict, *, agent_key: str,
                    payment_tx: Optional[str] = None,
                    api_key: Optional[str] = None) -> dict:
    # Normalize: drop None/empty so the intent memo is stable across equal calls.
    params = {k: v for k, v in (filters or {}).items() if v not in (None, "")}
    decision = await payment_gate.precheck("search_contracts", params, agent_key,
                                           payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]
    rows = await supa.search_contracts(**params)
    result = {"results": rows, "count": len(rows), "billing": _billing(decision)}
    # Provenance attestation (additive; fail-open; off the event loop).
    result["provenance"] = await asyncio.to_thread(
        mint_integration.attest_data, result, "analysis", "search_contracts query result")
    return result


async def do_detail(solicitation_number: str) -> dict:
    if not solicitation_number:
        return {"error": "bad_request", "detail": "solicitation_number is required"}
    row = await supa.contract_by_solicitation(solicitation_number)
    if not row:
        return {"error": "not_found",
                "detail": f"No contract with solicitation_number={solicitation_number!r}"}
    return {"contract": row}


async def do_spending(agency: str, fiscal_year: Optional[int], *, agent_key: str,
                      payment_tx: Optional[str] = None,
                      api_key: Optional[str] = None) -> dict:
    if not agency:
        return {"error": "bad_request", "detail": "agency is required"}
    params = {"agency": agency, "fiscal_year": fiscal_year}
    params = {k: v for k, v in params.items() if v not in (None, "")}
    decision = await payment_gate.precheck("agency_spending", params, agent_key,
                                           payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]
    data = await usaspending.agency_spending(agency, fiscal_year)
    data["billing"] = _billing(decision)
    return data


async def do_trending(naics: Optional[str], days: int, *, agent_key: str,
                      payment_tx: Optional[str] = None,
                      api_key: Optional[str] = None) -> dict:
    days = min(max(int(days or 30), 1), 365)
    params = {"naics": naics, "days": days}
    params = {k: v for k, v in params.items() if v not in (None, "")}
    decision = await payment_gate.precheck("trending_opportunities", params, agent_key,
                                           payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]

    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    rows = await supa.recent_opportunities(naics=naics, posted_after=since)
    buckets: dict[str, dict] = {}
    for r in rows:
        code = r.get("naics_code")
        if not code:
            continue  # trending is about NAICS sectors; skip uncoded notices
        b = buckets.setdefault(code, {
            "naics_code": code,
            "naics_description": r.get("naics_description"),
            "new_solicitations": 0,
            "agencies": set(),
        })
        b["new_solicitations"] += 1
        if r.get("agency"):
            b["agencies"].add(r["agency"])
        if not b["naics_description"] and r.get("naics_description"):
            b["naics_description"] = r["naics_description"]
    sectors = sorted(buckets.values(), key=lambda x: x["new_solicitations"], reverse=True)
    for b in sectors:
        b["agency_count"] = len(b["agencies"])
        b["top_agencies"] = sorted(b.pop("agencies"))[:5]
    return {
        "since": since, "days": days,
        "total_new_solicitations": len(rows),
        "sectors": sectors[:25],
        "billing": _billing(decision),
    }


async def do_daily_brief(date: Optional[str], *, agent_key: str,
                         payment_tx: Optional[str] = None,
                         api_key: Optional[str] = None) -> dict:
    """The curated daily gov-contracts brief ($10): top solicitations, approaching
    deadlines, major awards, and trending sectors — read back from the stored row
    the daily_curator generates at BRIEF_HOUR_UTC. Premium; gated like the other
    paid tools, then attested for provenance by the curator."""
    day = (date or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    decision = await payment_gate.precheck("daily_brief", {"date": day}, agent_key,
                                           payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]
    brief = await daily_curator.get_brief(day)
    if not brief:
        return {"error": "not_available",
                "detail": (f"No brief for {day} (not yet generated, or expired at "
                           f"midnight UTC). Briefs are curated daily at "
                           f"{config.BRIEF_HOUR_UTC:02d}:00 UTC."),
                "price_usdc": config.PRICE_DAILY_BRIEF,
                "billing": _billing(decision)}
    await daily_curator.bump_purchase(day)
    return {**brief, "price_usdc": config.PRICE_DAILY_BRIEF, "billing": _billing(decision)}


def mint_info() -> dict:
    """FoundryNet Data Network + MINT Protocol cross-promo (reads SISTER_SERVERS)."""
    return {
        "network": "FoundryNet Data Network", **mint_integration.network_feed_block(),
        "message": ("Attest your agent's government-contract / federal-procurement "
                    "analysis with MINT Protocol for verifiable on-chain proof."),
        "mint_protocol": {
            "mcp_endpoint": config.MINT_MCP_URL,
            "info_url": config.MINT_INFO_URL,
            "tools": ["mint_register", "mint_attest", "mint_verify",
                      "mint_rate", "mint_recommend", "mint_discover"],
        },
        "see_also": config.SISTER_SERVERS,
    }


# ── Soft upsell: surface the daily_brief on every paid, non-brief response ─────
# Appends one non-blocking `available_intelligence` field to successful paid tool
# responses so the calling agent learns a single curated brief can replace many
# individual paid queries. Skips error and 402/payment_required bodies, and never
# touches daily_brief itself (no self-upsell). Brief status is cached 5 min, so
# this adds no per-call DB latency. Added 2026-06-20 (seller_agent v2 upsell hook).
import time as _upsell_time

_brief_upsell_cache = {"day": None, "ts": 0.0, "available": False, "count": 0}


async def _brief_status_cached() -> tuple[bool, int]:
    day = _upsell_time.strftime("%Y-%m-%d", _upsell_time.gmtime())
    now = _upsell_time.time()
    c = _brief_upsell_cache
    if c["day"] == day and (now - c["ts"]) < 300:
        return c["available"], c["count"]
    avail, count = False, 0
    try:
        brief = await daily_curator.get_brief(day)
        if brief:
            avail, count = True, int(brief.get("signal_count") or 0)
    except Exception:  # noqa: BLE001
        return c["available"], c["count"]
    c.update(day=day, ts=now, available=avail, count=count)
    return avail, count


async def _available_intelligence() -> dict:
    avail, count = await _brief_status_cached()
    return {"daily_brief": {
        "available": avail,
        "signal_count": count,
        "price_usd": config.PRICE_DAILY_BRIEF,
        "tool": "daily_brief",
        "note": "Curated daily intelligence — more efficient than individual queries",
    }}


def _make_upsell(_fn):
    import functools

    @functools.wraps(_fn)
    async def _wrapped(*a, **k):
        result = await _fn(*a, **k)
        if isinstance(result, dict) and "error" not in result and "payment_required" not in result:
            try:
                result["available_intelligence"] = await _available_intelligence()
            except Exception:  # noqa: BLE001
                pass
            try:
                import asyncio as _aio, mint_integration as _mint
                result["foundrynet_network"] = await _aio.to_thread(_mint.network_heartbeat)
            except Exception:  # noqa: BLE001
                pass
        return result

    return _wrapped


for _upsell_fn in ("do_search", "do_spending", "do_trending",):
    if _upsell_fn in globals():
        globals()[_upsell_fn] = _make_upsell(globals()[_upsell_fn])
