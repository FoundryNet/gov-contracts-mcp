from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def agency_spending(
        agency: str,
        fiscal_year: Optional[int] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Spending summary for a federal agency: contract dollars broken down by
        NAICS sector and the top awardees, computed live from USASpending.gov for
        the requested fiscal year (the full federal record, not just the local
        table).

        PAID: $0.01 USDC per query after the daily free allowance. On a 402, pay
        the returned Solana memo and re-call with the SAME arguments plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            agency: awarding toptier agency name as USASpending labels it
                (e.g. "Department of Defense", "Department of Commerce").
            fiscal_year: U.S. federal FY (e.g. 2026). Defaults to the current FY.
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_spending(
            agency, fiscal_year,
            agent_key=identity.resolve_agent_key(agent_id),
            payment_tx=payment_tx,
            api_key=identity.bearer(),
        )
