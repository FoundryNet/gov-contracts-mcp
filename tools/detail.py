import core


def register(mcp) -> None:
    @mcp.tool
    async def contract_detail(solicitation_number: str) -> dict:
        """Get the full record for one U.S. federal government contract or
        solicitation by its solicitation number (for awards, the USASpending
        generated_internal_id) — including agency, NAICS code, value, and response
        deadline from SAM.gov / USASpending. FREE — no payment and no free-tier
        consumption.

        Args:
            solicitation_number: the natural key from a search_contracts result.
        """
        return await core.do_detail(solicitation_number)
