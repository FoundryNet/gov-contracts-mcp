"""Env-driven configuration for gov-contracts-mcp.

Single source of truth for every external dependency. Nothing reads a secret at
import time beyond os.environ; values are plain module globals so tools/clients
can `from config import SUPABASE_URL` without a settings object.

The MCP server is a thin query layer over the `gov_contracts` Supabase table that
the contract_aggregator agent fills every 6h. Three of the four tools are paid
(x402, $0.01 USDC on Solana); contract_detail is free. A free tier of
FREE_TIER_DAILY queries/day per agent precedes the paywall.

Required to be useful:
  SUPABASE_URL, SUPABASE_SERVICE_KEY   read access to gov_contracts + the
                                       free-tier counter / payment ledger tables.
Optional:
  PORT                Default 8080 (Railway injects this)
  REQUEST_TIMEOUT     HTTP timeout seconds, default 30
  X402_ENABLED        "true" arms the pay-per-query gate (DEFAULT true; kill
                      switch — "false" makes every tool free)
  QUERY_PRICE_USDC    Price per paid query in USDC, default "0.01"
  SOLANA_WALLET       base58 operations wallet receiving USDC (gate inert until set)
  PAYMENT_RECIPIENT   defaults to SOLANA_WALLET
  PAYMENT_VERIFY_RPC  Solana JSON-RPC used to confirm payment on-chain
  PAYMENT_USDC_MINT   SPL mint accepted (default = USDC mainnet)
  PAYMENT_EXPIRY_SECONDS  payment freshness / replay window, default 300
  FREE_TIER_DAILY     free paid-tool queries per agent per day, default 25
  PUBLIC_MCP_URL      public /mcp endpoint advertised in discovery payloads
"""
from __future__ import annotations

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _flag(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


# ── Supabase (the dataset + counters live here) ──────────────────────────────
SUPABASE_URL         = _env("SUPABASE_URL", "https://hjiozatcmozqddhaklkh.supabase.co").rstrip("/")
SUPABASE_SERVICE_KEY = _env("SUPABASE_SERVICE_KEY")   # service-role JWT (server-side only)

PORT            = int(_env("PORT", "8080"))
REQUEST_TIMEOUT = int(_env("REQUEST_TIMEOUT", "30"))

# ── x402 pay-per-query gate (payment_gate.py) ────────────────────────────────
# An agent pays QUERY_PRICE_USDC USDC on Solana (memo = the intent the 402
# returns), then retries the tool with payment_tx=<sig>; the gate confirms the
# transfer on-chain via plain JSON-RPC before the query runs. No solders /
# x402[svm] extra, so it can't crash-loop at boot. DEFAULT ON, fail-safe inert
# unless PAYMENT_RECIPIENT resolves to a wallet.
X402_ENABLED      = _flag("X402_ENABLED", True)
SOLANA_WALLET     = _env("SOLANA_WALLET", "wUumjWWvtFEr69qkTw3wHNVQVxLA8DTyJSyVgGmLThd")
QUERY_PRICE_USDC  = float(_env("QUERY_PRICE_USDC", "0.01"))
# Per-tool pricing. The gate charges TOOL_PRICES[tool] (USDC), falling back to
# QUERY_PRICE_USDC for any tool not listed. 0 = free (gate stays open for it).
TOOL_PRICES = {
    "daily_brief":            float(_env("PRICE_DAILY_BRIEF", "10")),
    "search_contracts":       float(_env("PRICE_SEARCH_CONTRACTS", "0.01")),
    "agency_spending":        float(_env("PRICE_AGENCY_SPENDING", "0.01")),
    "trending_opportunities": float(_env("PRICE_TRENDING_OPPORTUNITIES", "0.01")),
    "contract_detail":        float(_env("PRICE_CONTRACT_DETAIL", "0")),  # free
}
PAYMENT_RECIPIENT = _env("PAYMENT_RECIPIENT", SOLANA_WALLET).strip()
PAYMENT_VERIFY_RPC = _env("PAYMENT_VERIFY_RPC", "https://api.mainnet-beta.solana.com").rstrip("/")
# USDC on Solana mainnet (6 decimals). Override only for a different stable/network.
PAYMENT_USDC_MINT  = _env("PAYMENT_USDC_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v").strip()
PAYMENT_EXPIRY_SECONDS = int(_env("PAYMENT_EXPIRY_SECONDS", "300"))

# Free tier: this many paid-tool queries/day per agent before the paywall.
FREE_TIER_DAILY = int(_env("FREE_TIER_DAILY", "25"))

# ── Daily curated brief (daily_brief tool + daily_curator background task) ────
PRICE_DAILY_BRIEF = float(_env("PRICE_DAILY_BRIEF", "10"))   # premium curated brief
BRIEF_HOUR_UTC    = int(_env("BRIEF_HOUR_UTC", "5"))         # curator runs at 05:00 UTC
SERVER_SLUG       = "gov-contracts"
# Cross-network brief catalog (server -> price + tool) for related_briefs.
NETWORK_BRIEFS = {
    "financial-signals": "$25", "cyber-intel": "$15", "patent-intel": "$10",
    "gov-contracts": "$10", "compliance": "$10", "brand-intel": "$5", "weather-intel": "$5",
}

# ── MINT Protocol (data-provenance attestation + mint_info cross-promo) ──────
MINT_MCP_URL  = _env("MINT_MCP_URL", "https://mint-mcp-production.up.railway.app/mcp")
MINT_INFO_URL = _env("MINT_INFO_URL", "https://mint.foundrynet.io")

# Live source for agency_spending (no key needed).
USASPENDING_API = _env("USASPENDING_API", "https://api.usaspending.gov").rstrip("/")

# Public endpoint advertised in discovery payloads. Railway maps the service
# domain here once known.
PUBLIC_MCP_URL = _env("PUBLIC_MCP_URL", "https://gov-contracts-mcp-production.up.railway.app/mcp")

# ── FoundryNet Data Network — full sister-server map (auto-updated 2026-06-19) ──
# Re-binds SISTER_SERVERS to the complete network (all 11 servers, self excluded),
# now including fact-check-mcp, oss-intel-mcp, social-intel-mcp.
_FNET_ALL_SERVERS = {
    "mint-mcp":              "https://mint-mcp-production.up.railway.app/mcp",
    "foundrynet-mcp":        "https://foundrynet-mcp-production.up.railway.app/mcp",
    "gov-contracts-mcp":     "https://gov-contracts-mcp-production.up.railway.app/mcp",
    "brand-intel-mcp":       "https://brand-intel-mcp-production.up.railway.app/mcp",
    "patent-intel-mcp":      "https://patent-intel-mcp-production.up.railway.app/mcp",
    "financial-signals-mcp": "https://financial-signals-mcp-production.up.railway.app/mcp",
    "weather-intel-mcp":     "https://weather-intel-mcp-production.up.railway.app/mcp",
    "cyber-intel-mcp":       "https://cyber-intel-mcp-production.up.railway.app/mcp",
    "compliance-mcp":        "https://compliance-mcp-production.up.railway.app/mcp",
    "academic-intel-mcp":    "https://academic-intel-mcp-production.up.railway.app/mcp",
    "fact-check-mcp":        "https://fact-check-mcp-production.up.railway.app/mcp",
    "oss-intel-mcp":         "https://oss-intel-mcp-production.up.railway.app/mcp",
    "social-intel-mcp":      "https://social-intel-mcp-production.up.railway.app/mcp",
    "crypto-intel-mcp":      "https://crypto-intel-mcp-production.up.railway.app/mcp",
    "market-data-mcp":       "https://market-data-mcp-production.up.railway.app/mcp",
    "email-verify-mcp":      "https://email-verify-mcp-production.up.railway.app/mcp",
    "currency-intel-mcp":    "https://currency-intel-mcp-production.up.railway.app/mcp",
}
SISTER_SERVERS = {k: v for k, v in _FNET_ALL_SERVERS.items() if k != "gov-contracts-mcp"}
