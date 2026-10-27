# Step 2 data (saved so the raw trades never need re-buying)

The source is MES trades from Databento (GLBX.MDP3), regular hours 09:30–16:00 ET:
- 130 sessions, 27 Apr to 23 Oct 2026.
- Each session uses its own front contract: MESM6, then MESU6, then MESZ6.
- Bought 27 Oct 2026 for $44.54 (see `data-spend.md`).

Prices are in each contract's own prices, not shifted for rolls. To line them up with the level study's adjusted prices, add `offset_points` from `research/results/level_study/sessions.csv`.

| File | What it holds |
|---|---|
| `compact/<month>_1s_bars.parquet` | One row per second with trading: `session`, `contract`, `sec` (Unix seconds), `open`/`high`/`low`/`close` in ticks (× 0.25 = price), `buy` (aggressive buy contracts), `sell` (aggressive sell contracts), `trades` |
| `compact/<month>_footprint_1min.parquet` | Volume at each price per minute: `session`, `contract`, `minute` (Unix seconds), `price` in ticks, `buy`, `sell` |
| `bursts_50plus.csv.gz` | Every burst of 50+ contracts at one price by one aggressor side within 0.01 s (Bookmap absorption logic). `price` is in adjusted ticks; subtract `offset_ticks` for the contract's own price. `colour`: purple = aggressive buys absorbed by sellers, green = the reverse. |

Checked against the raw trades for 21 Jul 2026: buy volume, sell volume, high and low all match exactly.

Rebuild from raw trades (needs a new download): `python -m research.download_backtest_trades`, then `python -m research.compact_trades` and `python -m research.absorption_study`.
