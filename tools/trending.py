from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def trending_opportunities(
        naics: Optional[str] = None,
        days: int = 30,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Analyze which sectors are seeing the most new U.S. federal government
        solicitations right now — buckets SAM.gov opportunities posted in the last
        `days` by NAICS code and ranks them by new-solicitation volume, with the
        agencies driving each. Surfaces emerging federal procurement demand.

        PAID: $0.01 USDC per query after the daily free allowance. On a 402, pay
        the returned Solana memo and re-call with the SAME arguments plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            naics: optional exact 6-digit NAICS code to restrict to one sector.
            days: look-back window in days (1-365, default 30).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_trending(
            naics, days,
            agent_key=identity.resolve_agent_key(agent_id),
            payment_tx=payment_tx,
            api_key=identity.bearer(),
        )
