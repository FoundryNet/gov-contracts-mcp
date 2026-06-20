from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def search_contracts(
        keyword: Optional[str] = None,
        agency: Optional[str] = None,
        naics: Optional[str] = None,
        state: Optional[str] = None,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        status: Optional[str] = None,
        posted_after: Optional[str] = None,
        limit: int = 25,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Search U.S. federal government contracts, solicitations, and awards by
        agency, value, NAICS code, keyword, state, or status — live from SAM.gov
        opportunities and USASpending/FPDS awards, newest-first.

        Covers federal procurement and government spending across one aggregated,
        deduplicated dataset of open SAM.gov solicitations and USASpending contract
        awards.

        PAID: $0.01 USDC per query after a daily free allowance. The first calls
        each day are free; once spent, the tool returns an HTTP-402 body with
        Solana payment instructions and a memo — pay it, then call again with the
        SAME arguments plus payment_tx=<signature>. Pass agent_id to scope your own
        free allowance; an Authorization: Bearer fnet_ key bypasses the paywall.

        Args:
            keyword: free-text matched against title + description (ILIKE).
            agency: awarding agency name, partial match (e.g. "Defense").
            naics: exact 6-digit NAICS code (e.g. "334517").
            state: 2-letter place-of-performance state code (e.g. "TX").
            min_value: minimum award amount (USD).
            max_value: maximum award amount (USD).
            status: one of "active", "closed", "awarded".
            posted_after: ISO date "YYYY-MM-DD"; only records posted on/after it.
            limit: max rows (1-100, default 25).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        filters = {
            "keyword": keyword, "agency": agency, "naics": naics, "state": state,
            "min_value": min_value, "max_value": max_value, "status": status,
            "posted_after": posted_after, "limit": limit,
        }
        return await core.do_search(
            filters,
            agent_key=identity.resolve_agent_key(agent_id),
            payment_tx=payment_tx,
            api_key=identity.bearer(),
        )
