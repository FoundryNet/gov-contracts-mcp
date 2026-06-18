"""Shared business logic behind BOTH the MCP tools and the REST routes, so the
paywall + query behaviour never drift between surfaces.

Three paid operations (search / agency_spending / trending) run the x402 gate via
payment_gate.precheck before querying; contract_detail is free. Each returns plain
dicts/lists — the MCP tool returns them directly, the REST route maps them to
JSON (402 on a payment_required body).
"""
from __future__ import annotations

import time
from typing import Optional

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
    return {"results": rows, "count": len(rows), "billing": _billing(decision)}


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
