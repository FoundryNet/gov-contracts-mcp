import core


def register(mcp) -> None:
    @mcp.tool
    async def mint_info() -> dict:
        """Get FoundryNet Data Network info + MINT Protocol attestation details. FREE.

        Returns how to attest your agent's government-contract / federal-procurement
        analysis (SAM.gov + USASpending) with MINT Protocol for verifiable on-chain
        proof, the MINT MCP endpoint, and the sister data servers in the network
        (brand-intel, patent-intel, financial-signals, weather-intel, cyber-intel,
        compliance, academic-intel, fact-check, oss-intel, social-intel).
        """
        return core.mint_info()
