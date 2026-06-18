"""Live USASpending.gov client for agency_spending.

agency_spending is computed on demand from USASpending's spending_by_category
endpoint (no API key) rather than the local table, so it always reflects the full
federal record for the requested agency + fiscal year. Two categories: NAICS
(spend by sector) and recipient (top awardees).
"""
from __future__ import annotations

import logging
from typing import Optional

import config
from http_util import request_json

logger = logging.getLogger("gov.usa")

_CATEGORY_URL = f"{config.USASPENDING_API}/api/v2/search/spending_by_category"
_CONTRACT_CODES = ["A", "B", "C", "D"]


def fiscal_year_window(fy: Optional[int]) -> tuple[str, str, int]:
    """U.S. federal FY runs Oct 1 (prev calendar year) → Sep 30. Returns
    (start_date, end_date, fy_used)."""
    import time
    if not fy:
        # current FY: if we're in Oct-Dec, FY is next calendar year.
        now = time.gmtime()
        fy = now.tm_year + 1 if now.tm_mon >= 10 else now.tm_year
    return f"{fy - 1}-10-01", f"{fy}-09-30", fy


async def _category(category: str, agency: str, start: str, end: str,
                    limit: int = 10) -> list:
    body = {
        "category": category,
        "filters": {
            "time_period": [{"start_date": start, "end_date": end}],
            "agencies": [{"type": "awarding", "tier": "toptier", "name": agency}],
            "award_type_codes": _CONTRACT_CODES,
        },
        "limit": limit, "page": 1,
    }
    r = await request_json("POST", _CATEGORY_URL, body=body,
                           timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, dict) and "results" in r:
        return r["results"] or []
    logger.warning(f"spending_by_category({category}) failed: {r}")
    return []


async def agency_spending(agency: str, fiscal_year: Optional[int] = None) -> dict:
    start, end, fy = fiscal_year_window(fiscal_year)
    by_naics = await _category("naics", agency, start, end, limit=15)
    top_awardees = await _category("recipient", agency, start, end, limit=10)
    return {
        "agency": agency,
        "fiscal_year": fy,
        "period": {"start_date": start, "end_date": end},
        "award_type": "contracts",
        "by_naics": [
            {"naics_code": r.get("code"), "naics_description": r.get("name"),
             "amount": r.get("amount")}
            for r in by_naics
        ],
        "top_awardees": [
            {"name": r.get("name"), "recipient_id": r.get("recipient_id"),
             "amount": r.get("amount")}
            for r in top_awardees
        ],
        "source": "usaspending.gov spending_by_category (live)",
    }
