import core


def register(mcp) -> None:
    @mcp.tool
    async def contract_detail(solicitation_number: str) -> dict:
        """Fetch the full record for a single contract by its solicitation number
        (for awards, the USASpending generated_internal_id). FREE — no payment and
        no free-tier consumption.

        Args:
            solicitation_number: the natural key from a search_contracts result.
        """
        return await core.do_detail(solicitation_number)
