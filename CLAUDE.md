# CLAUDE.md

Instructions for Claude sessions working in this repo.

## Project

This repo builds an automated day trading bot for MES (Micro E-mini S&P 500) futures. The bot trades reversals at high-volume price levels (clusters of past daily POCs and the volume profile) when the order flow shows absorption.

**`STRATEGY.md` is the source of truth.** Read it before starting any work.

- When the owner changes a rule, update `STRATEGY.md` in the same change.
- Items marked TBD are still open. Don't silently pick values for them. Propose a default and say so.

## Working with the owner

- The owner is an experienced discretionary order flow trader who uses Bookmap and NinjaTrader. They are not a programmer.
- Explain results in trading terms (points, dollars, win rate). Keep messages short and avoid code jargon.
- The owner makes the calls on strategy rules. When a rule is unclear, ask instead of guessing.

## Build plan (from STRATEGY.md)

1. **Level study (Python):**
   - Do POC clusters stall price more often than other prices?
   - Does price cross low-volume gaps faster?
   - Which long-term window works best?
2. **Strategy backtest**, using trade data that includes the aggressor side.
3. **Order book signals**, using order book data for selected days only.
4. **Live bot:** a NinjaScript strategy running in NinjaTrader 8 on the Rithmic feed. Test it on Market Replay first, then a Sim account, then live with minimum size.

Never write or run anything that places real orders without the owner's explicit approval.

## Historical data: Databento

- Use dataset `GLBX.MDP3` (CME Globex).
  - Start with the `trades` schema, which includes the aggressor side.
  - Use order book schemas (`mbp-10`, `mbo`) only for specific days.
- **Authentication:** the sandbox proxy adds the Databento key (HTTP Basic auth) to requests sent to `hist.databento.com`.
  - The key is not visible in the session. Never ask the owner to paste it.
  - Call the HTTP API (`https://hist.databento.com/v0/...`) without credentials.
  - The `databento` Python package can still be used to decode DBN files.
  - If requests fail with 401 or 403, ask the owner to check the secret in the environment settings. If the host is blocked, the environment's network settings need to allow it.

### Cost rules (important)

- The account runs on a **$125 free credit**, which expires about 6 months after signup (around April 2027). There is no payment plan.
- **Before every download, get the price with `metadata.get_cost` (free) and show it to the owner. Download only after they approve.**
- Log every paid request in `data-spend.md`: the date, what was requested, the cost, and the estimated credit remaining.
- Request only what is needed: one symbol, the needed schema and the needed dates. Test a request on a single day first.
- The sandbox is wiped between sessions, and downloading again costs money again.
  - Commit compact derived results (daily volume profiles, POCs, signal tables) to the repo when they are small (a few MB).
  - Keep raw data in `data/`, which is gitignored.

## Market details

- **Contract:** MES. Tick size 0.25 = $1.25. $5 per point per contract. The plan trades 10 contracts.
- **Session:** the full Globex session, 18:00–17:00 ET. The Sunday 18:00 open belongs to Monday's session.
- **Timestamps:** Databento timestamps are UTC nanoseconds. Convert them with `America/New_York` so daylight saving time is handled.
- **Contract rolls:** contracts roll quarterly (H, M, U, Z).
  - Don't mix contracts within one session's profile.
  - Databento continuous symbols (for example `MES.v.0` with `stype_in=continuous`) follow the most active contract.

## Test case

Example 1 in `STRATEGY.md` is MES Jun25, 3 Apr 2025, 09:30–09:47 ET.

- Rebuilding the absorption indicator (100 or more contracts within 0.01 s) on this window must reproduce the three seller markers:
  - 118 at about 5540
  - 212 at about 5535.75
  - 109 at about 5532.75
- Use this window to settle whether purple markers mean absorbed aggressive buying or aggressive selling.

## Code conventions

- **Python** for research and backtests, under `research/`. Tests go under `tests/` and run with pytest.
- **Settings:** all strategy numbers live in one settings file that mirrors `STRATEGY.md` section 5. Don't hard-code thresholds in the logic.
- **Backtest costs:** include slippage (1–2 ticks on entries and stops) and commissions.
- **NinjaScript (C#)** goes under `ninjatrader/` later. It can't be compiled here; the owner compiles it in NinjaTrader.

## Git

- Work on a branch and push it. Ask the owner before merging into `main`.
- Never commit raw market data or credentials.
