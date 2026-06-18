"""Pay-per-query gate for the paid gov-contracts tools — on-chain USDC
micropayments on Solana, fronted by a daily free tier.

THE FLOW (HTTP 402, memo-bound, verified on-chain — no facilitator):
  1. A paid tool (search_contracts / agency_spending / trending_opportunities) is
     called. If the caller is under the daily free allowance (FREE_TIER_DAILY per
     agent), it runs free and the slot is consumed.
  2. Once the allowance is spent, the gate returns a 402 telling the agent to send
     QUERY_PRICE_USDC USDC to PAYMENT_RECIPIENT with a specific `memo` (a payment
     *intent* derived deterministically from the tool + its args — see intent_id).
  3. The agent makes the USDC transfer with that memo, then retries the tool with
     the SAME args + payment_tx=<signature>.
  4. The gate confirms on-chain via plain JSON-RPC: tx confirmed, ≥ price USDC to
     the wallet, memo matches, within PAYMENT_EXPIRY_SECONDS, signature unused.
  5. Only then does the query run.

contract_detail is FREE and never calls this gate. A caller presenting an
`Authorization: Bearer fnet_…` key also bypasses the gate (trusted/unlimited).

PERSISTENCE: free-tier counts live in Supabase `gov_query_usage` (atomic via the
gov_claim_free_query RPC); verified payments in `gov_payments` (UNIQUE tx_signature
= double-spend guard). When Supabase is unconfigured both fall back in-process
(single instance only).

SAFETY: httpx JSON-RPC only — no solders / x402[svm] extra — so it can't crash at
boot. DEFAULT ON but fail-safe inert unless PAYMENT_RECIPIENT is set;
X402_ENABLED=false is the kill switch.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Optional

import config
import supa
from http_util import request_json

logger = logging.getLogger("gov.pay")

_USDC_DECIMALS = 6

_MEMO_PROGRAM_IDS = frozenset({
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
})

# In-memory fallbacks (single instance only; production uses Supabase).
_mem_used_tx: dict = {}              # tx_signature -> row
_mem_free: dict = {}                 # (agent_key, day) -> count


# ── activation ────────────────────────────────────────────────────────────────
def is_active() -> bool:
    return bool(config.X402_ENABLED and config.PAYMENT_RECIPIENT)


def _expected_base_units() -> int:
    return round(config.QUERY_PRICE_USDC * (10 ** _USDC_DECIMALS))


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


# ── payment intent (the memo) ─────────────────────────────────────────────────
def intent_id(tool: str, params: dict) -> str:
    """Deterministic 32-hex memo for one query. The agent gets it in the 402, puts
    it on the USDC tx, and resends the identical tool + args — so the server
    recomputes the same memo without storing a quote."""
    canonical = json.dumps({"tool": tool, "params": params or {}},
                           sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def payment_required_body(tool: str, intent: str, reason: Optional[str] = None) -> dict:
    body = {
        "status": 402,
        "error": "payment_required",
        "payment_required": {
            "amount": f"{config.QUERY_PRICE_USDC:.2f}",
            "currency": "USDC",
            "network": "solana",
            "recipient": config.PAYMENT_RECIPIENT,
            "memo": intent,
            "expires_in": config.PAYMENT_EXPIRY_SECONDS,
            "usdc_mint": config.PAYMENT_USDC_MINT,
            "amount_base_units": _expected_base_units(),
            "decimals": _USDC_DECIMALS,
        },
        "instructions": (
            f"Daily free tier ({config.FREE_TIER_DAILY} queries) is spent. Send "
            f"{config.QUERY_PRICE_USDC:.2f} USDC ({config.PAYMENT_USDC_MINT}) to "
            f"{config.PAYMENT_RECIPIENT} on Solana with the SPL-memo set to "
            f"'{intent}', then call {tool} again with the SAME arguments plus "
            f"payment_tx=<transaction signature>."),
    }
    if reason:
        body["reason"] = reason
    return body


# ── on-chain verification (plain Solana JSON-RPC) ─────────────────────────────
def _fail(code: str, detail: str) -> dict:
    return {"ok": False, "reason": code, "detail": detail}


async def verify_payment(tx_signature: str, expected_memo: str) -> dict:
    rpc = {
        "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
        "params": [tx_signature, {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
        }],
    }
    resp = await request_json("POST", config.PAYMENT_VERIFY_RPC, body=rpc,
                              timeout=config.REQUEST_TIMEOUT)
    if not isinstance(resp, dict) or "error" in resp:
        return _fail("rpc_error", f"Solana RPC call failed: {resp.get('detail') if isinstance(resp, dict) else resp}")
    if resp.get("error"):
        return _fail("rpc_error", f"Solana RPC error: {resp['error']}")
    result = resp.get("result")
    if result is None:
        return _fail("not_confirmed",
                     "Transaction not found or not yet confirmed. Wait, then retry "
                     "with the same payment_tx.")
    meta = result.get("meta") or {}
    if meta.get("err") is not None:
        return _fail("tx_failed", f"Transaction failed on-chain: {meta.get('err')}")

    block_time = result.get("blockTime")
    if block_time is None:
        return _fail("not_confirmed", "Transaction has no blockTime yet (still processing).")
    age = time.time() - block_time
    if age > config.PAYMENT_EXPIRY_SECONDS:
        return _fail("expired",
                     f"Payment is {int(age)}s old; must be within "
                     f"{config.PAYMENT_EXPIRY_SECONDS}s. Make a fresh payment.")
    if age < -120:
        return _fail("clock_skew", "Transaction blockTime is in the future (clock skew).")

    delta = _usdc_delta_to_recipient(meta)
    if delta is None:
        return _fail("no_transfer",
                     f"No USDC transfer to the operations wallet "
                     f"{config.PAYMENT_RECIPIENT} found in this tx.")
    need = _expected_base_units()
    if delta < need:
        return _fail("underpaid",
                     f"Transferred {delta / 10**_USDC_DECIMALS:.6f} USDC; need at "
                     f"least {config.QUERY_PRICE_USDC:.2f} USDC.")

    memo = _extract_memo(result, meta)
    if not memo or expected_memo not in memo:
        return _fail("memo_mismatch",
                     f"Payment memo {memo!r} does not contain the required intent "
                     f"'{expected_memo}'. Pay with that exact memo.")

    return {"ok": True, "amount_base": delta, "amount_usdc": delta / 10**_USDC_DECIMALS,
            "payer": _payer(result), "block_time": block_time}


def _usdc_delta_to_recipient(meta: dict) -> Optional[int]:
    mint, recip = config.PAYMENT_USDC_MINT, config.PAYMENT_RECIPIENT
    pre = {b.get("accountIndex"): b for b in (meta.get("preTokenBalances") or [])}
    post = {b.get("accountIndex"): b for b in (meta.get("postTokenBalances") or [])}
    best: Optional[int] = None
    for idx, pb in post.items():
        if pb.get("mint") != mint or pb.get("owner") != recip:
            continue
        post_amt = int(pb.get("uiTokenAmount", {}).get("amount", 0))
        pre_amt = int((pre.get(idx) or {}).get("uiTokenAmount", {}).get("amount", 0))
        d = post_amt - pre_amt
        best = d if best is None or d > best else best
    return best


def _extract_memo(result: dict, meta: dict) -> Optional[str]:
    msg = (result.get("transaction") or {}).get("message") or {}
    instrs = list(msg.get("instructions") or [])
    for inner in (meta.get("innerInstructions") or []):
        instrs.extend(inner.get("instructions") or [])
    for ins in instrs:
        if ins.get("program") == "spl-memo" or ins.get("programId") in _MEMO_PROGRAM_IDS:
            p = ins.get("parsed")
            if isinstance(p, str):
                return p
            if isinstance(p, dict):
                return p.get("memo") or p.get("info")
    for line in (meta.get("logMessages") or []):
        m = re.search(r'Memo \(len \d+\): "(.*)"', line)
        if m:
            return m.group(1)
    return None


def _payer(result: dict) -> Optional[str]:
    keys = ((result.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    if keys:
        first = keys[0]
        return first.get("pubkey") if isinstance(first, dict) else first
    return None


# ── free-tier + payment stores (Supabase, with in-memory fallback) ────────────
async def _claim_free(agent_key: str) -> dict:
    """Return {allowed, count, cap}. Atomic in Supabase; best-effort in-memory."""
    day = _today()
    if supa.configured():
        r = await supa.claim_free_query(agent_key, day, config.FREE_TIER_DAILY)
        if r is not None:
            return r
        # fall through to in-memory if the RPC errored
    key = (agent_key, day)
    cur = _mem_free.get(key, 0)
    if cur < config.FREE_TIER_DAILY:
        _mem_free[key] = cur + 1
        return {"allowed": True, "count": cur + 1, "cap": config.FREE_TIER_DAILY}
    return {"allowed": False, "count": cur, "cap": config.FREE_TIER_DAILY}


async def _tx_used(tx_signature: str) -> bool:
    if supa.configured():
        return await supa.payment_tx_used(tx_signature)
    return tx_signature in _mem_used_tx


async def _reserve_payment(row: dict) -> bool:
    tx = row["tx_signature"]
    if supa.configured():
        res = await supa.insert_payment(row)
        if "error" in res:
            blob = json.dumps(res).lower()
            if "409" in blob or "duplicate" in blob or "unique" in blob:
                return False
            logger.error(f"payment ledger insert failed (treating as unreserved): {res}")
            return False
        return True
    if tx in _mem_used_tx:
        return False
    _mem_used_tx[tx] = row
    return True


# ── the gate (called by core.* behind BOTH MCP tools and REST routes) ─────────
def _has_api_key(api_key: Optional[str]) -> bool:
    return bool(api_key and api_key.strip())


async def precheck(tool: str, params: dict, agent_key: str,
                   payment_tx: Optional[str], api_key: Optional[str]) -> dict:
    """Decide whether a paid query may run. Returns a dict with `gate`:
      "open"    — gating inert (free for everyone)
      "api_key" — a Bearer fnet_ key is present (trusted/unlimited)
      "free"    — within the daily free allowance (slot consumed)
      "paid"    — payment verified on-chain and the tx claimed
      "blocked" — needs payment; carries {"status":402, "body": <402 payload>}
    """
    if not is_active():
        return {"gate": "open"}
    if _has_api_key(api_key):
        return {"gate": "api_key"}

    claim = await _claim_free(agent_key)
    if claim.get("allowed"):
        return {"gate": "free", "count": claim.get("count"), "cap": claim.get("cap")}

    intent = intent_id(tool, params)
    payment_tx = (payment_tx or "").strip()
    if not payment_tx:
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(tool, intent)}

    if await _tx_used(payment_tx):
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(
                    tool, intent, reason="This payment_tx was already used. Make a "
                                         "new payment.")}

    v = await verify_payment(payment_tx, intent)
    if not v["ok"]:
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(tool, intent, reason=v["detail"])}

    row = {
        "tx_signature": payment_tx, "intent": intent, "agent_key": agent_key,
        "tool": tool, "amount_usdc": v["amount_usdc"], "payer_wallet": v.get("payer"),
        "recipient": config.PAYMENT_RECIPIENT, "status": "settled",
        "block_time": v.get("block_time"),
    }
    if not await _reserve_payment(row):
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(
                    tool, intent, reason="This payment_tx was already used (claimed "
                                         "concurrently). Make a new payment.")}
    logger.info(f"x402 payment verified: {payment_tx} {v['amount_usdc']:.6f} USDC "
                f"from {v.get('payer')} for {tool}")
    return {"gate": "paid", "payment_tx": payment_tx, "amount_usdc": v["amount_usdc"]}
