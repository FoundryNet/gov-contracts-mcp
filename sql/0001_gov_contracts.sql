-- Government Contract Data Aggregation — schema for contract_aggregator.py +
-- gov-contracts-mcp. Idempotent; safe to re-run.
--
-- Two tables:
--   gov_contracts    — the aggregated dataset (SAM.gov opportunities + USASpending
--                      / FPDS awards), one normalized row shape for both sources.
--   gov_query_usage  — per-agent/day free-tier counter for the paid MCP tools.

create extension if not exists pg_trgm;

-- ── gov_contracts ────────────────────────────────────────────────────────────
create table if not exists gov_contracts (
  id                          uuid primary key default gen_random_uuid(),
  source                      text not null,        -- 'sam.gov' | 'usaspending'
  title                       text,
  description                 text,
  agency                      text,
  sub_agency                  text,
  naics_code                  text,
  naics_description           text,
  set_aside_type              text,
  solicitation_number         text,                 -- natural key per source (see below)
  posted_date                 date,
  response_deadline           timestamptz,
  award_amount                numeric,
  awardee_name                text,
  awardee_duns                text,
  place_of_performance_state  text,
  place_of_performance_city   text,
  contract_type               text,
  status                      text,                 -- active | closed | awarded
  source_url                  text,
  created_at                  timestamptz not null default now(),
  updated_at                  timestamptz not null default now(),
  -- Dedup key. For SAM.gov this is solicitationNumber (fallback noticeId); for
  -- USASpending it is generated_internal_id. The aggregator always writes a
  -- non-null value, so the UNIQUE pair is the upsert target (on_conflict).
  unique (source, solicitation_number)
);

create index if not exists idx_gov_contracts_posted  on gov_contracts (posted_date desc nulls last);
create index if not exists idx_gov_contracts_agency  on gov_contracts (agency);
create index if not exists idx_gov_contracts_naics   on gov_contracts (naics_code);
create index if not exists idx_gov_contracts_state   on gov_contracts (place_of_performance_state);
create index if not exists idx_gov_contracts_status  on gov_contracts (status);
-- Trigram index so keyword ILIKE search on title stays fast as the table grows.
create index if not exists idx_gov_contracts_title_trgm on gov_contracts using gin (title gin_trgm_ops);

-- ── gov_query_usage (free-tier counter for the paid MCP tools) ────────────────
create table if not exists gov_query_usage (
  agent_key   text not null,     -- agent_id arg, else sha256(client_ip)
  day         date not null,
  count       integer not null default 0,
  updated_at  timestamptz not null default now(),
  primary key (agent_key, day)
);

-- Verified x402 payments (double-spend guard + revenue ledger). UNIQUE
-- tx_signature is the guard: a duplicate insert (409) means the tx was already
-- claimed for a query.
create table if not exists gov_payments (
  tx_signature  text primary key,
  intent        text,
  agent_key     text,
  tool          text,
  amount_usdc   numeric,
  payer_wallet  text,
  recipient     text,
  status        text,
  block_time    bigint,
  created_at    timestamptz not null default now()
);

-- Atomically claim ONE free query for (agent_key, day) only if under the cap.
-- Returns {allowed, count, cap}. Paid calls never touch this counter — so the
-- free allowance and the x402 revenue path stay cleanly separated.
create or replace function gov_claim_free_query(p_agent_key text, p_day date, p_cap integer)
returns jsonb
language plpgsql
as $$
declare cur integer; ok boolean;
begin
  insert into gov_query_usage (agent_key, day, count, updated_at)
  values (p_agent_key, p_day, 0, now())
  on conflict (agent_key, day) do nothing;

  select count into cur from gov_query_usage
    where agent_key = p_agent_key and day = p_day for update;

  if cur < p_cap then
    update gov_query_usage set count = count + 1, updated_at = now()
      where agent_key = p_agent_key and day = p_day;
    ok := true;
    cur := cur + 1;
  else
    ok := false;
  end if;
  return jsonb_build_object('allowed', ok, 'count', cur, 'cap', p_cap);
end;
$$;
