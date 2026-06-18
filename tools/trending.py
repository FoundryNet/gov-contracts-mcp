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
        """Which sectors are seeing the most new federal solicitations right now.
        Buckets the opportunities posted in the last `days` by NAICS sector and
        ranks them by new-solicitation volume (with the agencies driving each).

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
