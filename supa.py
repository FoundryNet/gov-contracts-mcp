"""Supabase PostgREST client for gov-contracts-mcp.

Reads the `gov_contracts` dataset and backs the free-tier counter
(`gov_query_usage` via the `gov_claim_free_query` RPC) and the x402 payment
ledger (`gov_payments`, UNIQUE tx_signature = double-spend guard). Every helper
returns plain data and never raises — failures degrade to []/{}/False so a tool
call surfaces a clean result instead of crashing the MCP frame.
"""
from __future__ import annotations

import logging
from typing import Optional

import config
from http_util import request_json

logger = logging.getLogger("gov.supa")


def configured() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY)


def _headers(extra: Optional[dict] = None) -> dict:
    h = {
        "apikey":        config.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _url(path: str) -> str:
    return f"{config.SUPABASE_URL}/rest/v1/{path}"


async def _select(table: str, params: dict) -> list:
    if not configured():
        return []
    r = await request_json("GET", _url(table), headers=_headers(),
                           params=params, timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, list):
        return r
    logger.warning(f"supa select {table} failed: {r}")
    return []


async def _rpc(fn: str, body: dict):
    if not configured():
        return None
    r = await request_json("POST", _url(f"rpc/{fn}"), headers=_headers(),
                           body=body, timeout=config.REQUEST_TIMEOUT)
    return r


# ── gov_contracts reads ───────────────────────────────────────────────────────

# Columns returned to callers (the full row shape).
_FIELDS = ("id,source,title,description,agency,sub_agency,naics_code,"
           "naics_description,set_aside_type,solicitation_number,posted_date,"
           "response_deadline,award_amount,awardee_name,awardee_duns,"
           "place_of_performance_state,place_of_performance_city,contract_type,"
           "status,source_url,created_at,updated_at")


async def search_contracts(*, keyword=None, agency=None, naics=None, state=None,
                           min_value=None, max_value=None, status=None,
                           posted_after=None, limit=25) -> list:
    """Filtered, recency-sorted query over gov_contracts. All filters AND together
    via PostgREST query params."""
    params = {"select": _FIELDS, "order": "posted_date.desc.nullslast",
              "limit": str(min(max(int(limit or 25), 1), 100))}
    if keyword:
        # ILIKE across title + description (PostgREST or= for a logical OR).
        kw = keyword.replace("*", "").replace(",", " ")
        params["or"] = f"(title.ilike.*{kw}*,description.ilike.*{kw}*)"
    if agency:
        params["agency"] = f"ilike.*{agency}*"
    if naics:
        params["naics_code"] = f"eq.{naics}"
    if state:
        params["place_of_performance_state"] = f"eq.{state.upper()}"
    if status:
        params["status"] = f"eq.{status}"
    if posted_after:
        params["posted_date"] = f"gte.{posted_after}"
    if min_value is not None:
        params.setdefault("award_amount", "")
        params["award_amount"] = f"gte.{min_value}"
    if max_value is not None:
        # If both bounds set, PostgREST needs them as separate params; use and=.
        if min_value is not None:
            params.pop("award_amount", None)
            params["and"] = f"(award_amount.gte.{min_value},award_amount.lte.{max_value})"
        else:
            params["award_amount"] = f"lte.{max_value}"
    return await _select("gov_contracts", params)


async def contract_by_solicitation(solicitation_number: str) -> Optional[dict]:
    rows = await _select("gov_contracts", {
        "select": _FIELDS,
        "solicitation_number": f"eq.{solicitation_number}",
        "order": "updated_at.desc", "limit": "1"})
    return rows[0] if rows else None


async def recent_opportunities(*, naics=None, posted_after=None, max_rows=10000) -> list:
    """Minimal projection for trending: just the fields we aggregate on. Pages past
    PostgREST's 1000-row default cap (via Range headers) up to max_rows so the
    sector counts reflect the whole window, not just the latest 1000."""
    base = {"select": "naics_code,naics_description,posted_date,agency",
            "source": "eq.sam.gov", "order": "posted_date.desc.nullslast"}
    if posted_after:
        base["posted_date"] = f"gte.{posted_after}"
    if naics:
        base["naics_code"] = f"eq.{naics}"
    if not configured():
        return []
    out: list = []
    page = 1000
    for start in range(0, max_rows, page):
        end = start + page - 1
        r = await request_json(
            "GET", _url("gov_contracts"),
            headers=_headers({"Range-Unit": "items", "Range": f"{start}-{end}"}),
            params=base, timeout=config.REQUEST_TIMEOUT)
        if not isinstance(r, list):
            logger.warning(f"recent_opportunities page {start}-{end} failed: {r}")
            break
        out.extend(r)
        if len(r) < page:
            break
    return out


# ── free-tier counter ─────────────────────────────────────────────────────────

async def claim_free_query(agent_key: str, day: str, cap: int) -> Optional[dict]:
    """Atomically claim one free query for (agent_key, day) if under cap.
    Returns {allowed, count, cap} or None if Supabase is unconfigured / errored."""
    r = await _rpc("gov_claim_free_query",
                   {"p_agent_key": agent_key, "p_day": day, "p_cap": cap})
    if isinstance(r, dict) and "allowed" in r:
        return r
    if isinstance(r, list) and r and isinstance(r[0], dict):
        return r[0]
    logger.warning(f"claim_free_query rpc unexpected: {r}")
    return None


# ── payment ledger (double-spend guard) ───────────────────────────────────────

async def payment_tx_used(tx_signature: str) -> bool:
    rows = await _select("gov_payments",
                         {"tx_signature": f"eq.{tx_signature}", "select": "tx_signature",
                          "limit": "1"})
    return bool(rows)


async def insert_payment(row: dict) -> dict:
    if not configured():
        return {"error": "not_configured"}
    r = await request_json("POST", _url("gov_payments"),
                           headers=_headers({"Prefer": "return=minimal"}),
                           body=row, timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, list):
        return {"data": r}
    if isinstance(r, dict) and "error" not in r:
        return {"data": [r]}
    return r if isinstance(r, dict) else {"error": "bad_response", "detail": str(r)}
