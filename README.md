# Government Contracts MCP

**Government contract search and federal procurement data for AI agents.** Search
[SAM.gov](https://sam.gov) opportunities and [USASpending.gov](https://usaspending.gov)
/ FPDS contract awards from one aggregated, deduplicated dataset — plus contract
detail, agency spending breakdowns, and trending solicitation sectors.

Live MCP endpoint (Streamable HTTP):
`https://gov-contracts-mcp-production.up.railway.app/mcp`

## Connect

- **MCP endpoint:** `https://gov-contracts-mcp-production.up.railway.app/mcp`
- **Registry id:** `io.github.FoundryNet/gov-contracts-mcp`
- **Agent card:** `https://gov-contracts-mcp-production.up.railway.app/.well-known/agent-card.json`

### Claude Desktop / Cursor / Claude Code

```bash
claude mcp add --transport http gov-contracts https://gov-contracts-mcp-production.up.railway.app/mcp
```

## Tools

| Tool | Price | What it does |
|---|---|---|
| `search_contracts` | $0.01 | Filtered search of opportunities + awards (keyword, agency, NAICS, state, value range, status, posted-after), newest first |
| `contract_detail` | **free** | Full record for one solicitation number |
| `agency_spending` | $0.01 | Live spend-by-NAICS + top awardees for an agency / fiscal year |
| `trending_opportunities` | $0.01 | Sectors ranked by new-solicitation volume over a window |
| `daily_brief` | $10 | Curated daily federal-procurement brief (MINT-attested) |
| `mint_info` | **free** | FoundryNet Data Network + MINT Protocol cross-promo |

**Free tier:** 25 paid-tool queries/day per agent (plus unlimited free
`contract_detail`). Pass `agent_id` to scope your allowance. After that, each query
costs **$0.01 USDC on Solana** via [x402](https://x402.org): the tool returns an
HTTP-402 with a payment memo — send the USDC with that memo, then re-call with the
same arguments plus `payment_tx=<signature>`. An `Authorization: Bearer fnet_…` key
bypasses the paywall.

## Data sources

- **SAM.gov Opportunities API** — open solicitations / opportunity notices.
- **USASpending.gov Award API** — awarded contracts (FPDS data flows through here).

A background agent refreshes the dataset every 6 hours; `agency_spending` queries
USASpending live so it always reflects the full federal record.

## Connect

MCP registry:
`io.github.FoundryNet/gov-contracts-mcp`

```json
{
  "mcpServers": {
    "gov-contracts": {
      "url": "https://gov-contracts-mcp-production.up.railway.app/mcp"
    }
  }
}
```

## Develop

```bash
pip install -r requirements.txt
export SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
python server.py        # serves /mcp + /sse on :8080, /health for liveness
```

Apply `sql/0001_gov_contracts.sql` to the Supabase project before first run.

Built by [FoundryNet](https://foundrynet.io) · hello@foundrynet.io

## Live network activity

**Live feed:** [mint.foundrynet.io/feed](https://mint.foundrynet.io/feed)  
Real-time verified work across 13 servers and autonomous agents, anchored on Solana via [MINT Protocol](https://mint.foundrynet.io).
