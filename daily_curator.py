"""Daily curated brief — gov-contracts.

Runs once a day at BRIEF_HOUR_UTC (05:00 UTC) as an in-process background task
(same shape as the financial-signals curator). It queries the last 24h of the
`gov_contracts` dataset (SAM.gov opportunities + USASpending/FPDS awards), ranks
by significance, packages the day's top solicitations / approaching deadlines /
major awards / trending sectors, attests the package through MINT for verifiable
provenance, and upserts it into the `daily_briefs` table. The paid `daily_brief`
tool just reads that row back.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import config
import mint_integration
import supa

logger = logging.getLogger("gov.curator")

SERVER = config.SERVER_SLUG
PRICE = config.PRICE_DAILY_BRIEF

# Full row projection for curation (mirrors supa._FIELDS).
_FIELDS = ("id,source,title,description,agency,sub_agency,naics_code,"
           "naics_description,set_aside_type,solicitation_number,posted_date,"
           "response_deadline,award_amount,awardee_name,"
           "place_of_performance_state,contract_type,status,source_url,created_at")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _expires_at(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (d + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")


def related_briefs(exclude: str) -> list:
    return [{"server": s, "price": p, "tool": "daily_brief"}
            for s, p in config.NETWORK_BRIEFS.items() if s != exclude]


async def _recent_rows(since_iso: str, limit: int = 1000) -> list:
    """Rows created in the last 24h. If that window is sparse (< 5 rows), fall
    back to the most-recently-ingested rows so the brief is never empty."""
    rows = await supa.select("gov_contracts", {
        "select": _FIELDS,
        "created_at": f"gte.{since_iso}",
        "order": "created_at.desc",
        "limit": str(limit),
    })
    if len(rows) < 5:
        rows = await supa.select("gov_contracts", {
            "select": _FIELDS,
            "order": "created_at.desc",
            "limit": str(limit),
        })
    return rows


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


async def _curate_signals(since_iso: str) -> tuple[dict, int]:
    """Build the gov-contracts brief body from the last 24h (or most-recent if
    sparse). Returns (signals, count)."""
    rows = await _recent_rows(since_iso)

    # Opportunities = SAM.gov notices that are not yet awarded.
    opportunities = [r for r in rows
                     if r.get("source") == "sam.gov" or r.get("status") != "awarded"]
    awards = [r for r in rows if r.get("status") == "awarded" or r.get("awardee_name")]

    # top_solicitations: top 10 highest-value NEW solicitations.
    sols = sorted(opportunities, key=lambda r: _num(r.get("award_amount")), reverse=True)
    top_solicitations = [{
        "solicitation_number": r.get("solicitation_number"),
        "title": r.get("title"),
        "agency": r.get("agency"),
        "naics_code": r.get("naics_code"),
        "estimated_value": r.get("award_amount"),
        "response_deadline": r.get("response_deadline"),
        "posted_date": r.get("posted_date"),
        "source_url": r.get("source_url"),
    } for r in sols[:10]]

    # approaching_deadlines: open solicitations whose response deadline is in the
    # next 7 days (and not already past), soonest first.
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=7)
    dated = []
    for r in opportunities:
        dl = r.get("response_deadline")
        if not dl:
            continue
        try:
            d = datetime.fromisoformat(str(dl).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if now <= d <= horizon:
            dated.append((d, r))
    dated.sort(key=lambda t: t[0])
    approaching_deadlines = [{
        "solicitation_number": r.get("solicitation_number"),
        "title": r.get("title"),
        "agency": r.get("agency"),
        "response_deadline": r.get("response_deadline"),
        "naics_code": r.get("naics_code"),
    } for _, r in dated[:10]]

    # major_awards: major recent awards by award_amount.
    aw = sorted(awards, key=lambda r: _num(r.get("award_amount")), reverse=True)
    major_awards = [{
        "solicitation_number": r.get("solicitation_number"),
        "title": r.get("title"),
        "agency": r.get("agency"),
        "awardee_name": r.get("awardee_name"),
        "award_amount": r.get("award_amount"),
        "naics_code": r.get("naics_code"),
        "place_of_performance_state": r.get("place_of_performance_state"),
    } for r in aw if _num(r.get("award_amount")) > 0][:10]

    # trending_sectors: top NAICS sectors / agencies by new-notice volume.
    naics_buckets: dict[str, dict] = {}
    agency_counts: dict[str, int] = {}
    for r in rows:
        ag = r.get("agency")
        if ag:
            agency_counts[ag] = agency_counts.get(ag, 0) + 1
        code = r.get("naics_code")
        if not code:
            continue
        b = naics_buckets.setdefault(code, {
            "naics_code": code,
            "naics_description": r.get("naics_description"),
            "count": 0,
        })
        b["count"] += 1
        if not b["naics_description"] and r.get("naics_description"):
            b["naics_description"] = r.get("naics_description")
    trending_naics = sorted(naics_buckets.values(), key=lambda x: x["count"], reverse=True)[:10]
    top_agencies = sorted(agency_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
    trending_sectors = {
        "by_naics": trending_naics,
        "by_agency": [{"agency": a, "new_notices": c} for a, c in top_agencies],
    }

    signals = {
        "top_solicitations": top_solicitations,
        "approaching_deadlines": approaching_deadlines,
        "major_awards": major_awards,
        "trending_sectors": trending_sectors,
    }
    count = (len(top_solicitations) + len(approaching_deadlines) + len(major_awards)
             + len(trending_naics) + len(top_agencies))
    return signals, count


async def run_curation(date_str: str | None = None) -> dict:
    """Generate, attest, and store today's brief. Idempotent per date (upsert)."""
    date_str = date_str or _today()
    since_iso = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    signals, count = await _curate_signals(since_iso)

    brief = {
        "brief_date": date_str, "server": SERVER, "signal_count": count,
        "signals": signals, "expires_at": _expires_at(date_str),
        "related_briefs": related_briefs(SERVER),
    }
    # Attest for provenance (sync httpx → run off the event loop; fail-open).
    attestation = await asyncio.to_thread(
        mint_integration.attest_data, brief, "analysis",
        f"Daily {SERVER} brief: {count} signals")
    brief["provenance"] = attestation

    row = {
        "brief_date": date_str, "brief_data": brief, "signal_count": count,
        "attestation_hash": attestation.get("attestation_hash"),
        "expires_at": _expires_at(date_str),
    }
    res = await supa.upsert("daily_briefs", [row], "brief_date")
    if isinstance(res, dict) and res.get("error"):
        logger.warning(f"daily brief upsert failed: {str(res)[:200]}")
    else:
        logger.info(f"daily brief stored: {date_str} ({count} signals, "
                    f"attested={attestation.get('mint_verified')})")
    return brief


async def get_brief(date_str: str | None = None) -> dict | None:
    """Read a stored brief; None if missing or expired."""
    date_str = date_str or _today()
    rows = await supa.select("daily_briefs",
                             {"select": "*", "brief_date": f"eq.{date_str}", "limit": "1"})
    if not rows:
        return None
    row = rows[0]
    exp = row.get("expires_at")
    if exp:
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(exp.replace("Z", "+00:00")):
                return None
        except Exception:  # noqa: BLE001
            pass
    return row.get("brief_data")


async def bump_purchase(date_str: str) -> None:
    """Best-effort purchase counter via RPC (no-op if the function is absent)."""
    try:
        await supa.rpc("increment_brief_purchase", {"p_brief_date": date_str})
    except Exception:  # noqa: BLE001
        pass


async def curator_loop() -> None:
    """Sleep until BRIEF_HOUR_UTC each day, then curate. Cancellable."""
    while True:
        now = datetime.now(timezone.utc)
        secs = now.hour * 3600 + now.minute * 60 + now.second
        wait = (config.BRIEF_HOUR_UTC * 3600 - secs) % 86400 or 86400
        try:
            await asyncio.sleep(wait)
            if supa.configured():
                await run_curation()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"curator loop error: {e}")
            await asyncio.sleep(3600)
