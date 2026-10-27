"""Download the trade data the owner approved for the step 2 backtest (27 Oct 2026).

Approved: MES trades, regular hours (09:30-16:00 ET) only, sessions 27 Apr to
23 Oct 2026 (130 sessions), each session's own front contract. Quoted $44.19.

Prices every request first (free) and stops before downloading anything if
the total is above the approved amount. Logs one line in data-spend.md with
the total for whatever was downloaded.

Run from the repo root:   python -m research.download_backtest_trades
"""

from __future__ import annotations

from research.databento_http import REPO_ROOT, download, get_cost, log_spend
from research.trade_data_plan import request_ranges

FIRST, LAST = "2026-04-27", "2026-10-23"
APPROVED_TOTAL = 44.19
TOLERANCE = 0.25            # quotes can move by cents between pricing and download
OUT_DIR = REPO_ROOT / "data" / "raw" / "trades_rth"


def main() -> None:
    reqs = request_ranges(FIRST, LAST, "rth")
    todo = [(c, a, b) for c, a, b in reqs if not (OUT_DIR / f"{c}_{a[:10]}.dbn.zst").exists()]
    quotes = [get_cost(c, "trades", a, b, "raw_symbol") for c, a, b in todo]
    total = sum(quotes)
    print(f"{len(reqs)} sessions, {len(todo)} still to download, quoted ${total:.2f}")
    if total > APPROVED_TOTAL + TOLERANCE:
        raise SystemExit(f"Quote ${total:.2f} is above the approved ${APPROVED_TOTAL:.2f}. Nothing downloaded.")

    spent, done = 0.0, 0
    try:
        for (c, a, b), q in zip(todo, quotes):
            download(name=f"{c}_{a[:10]}", symbols=c, schema="trades", start=a, end=b,
                     stype_in="raw_symbol", approved_max_cost=q + 0.05, out_dir=OUT_DIR,
                     description="", log=False)
            spent += q
            done += 1
    finally:
        if done:
            log_spend(f"MES trades, regular hours 09:30-16:00 ET, {done} sessions (step 2 backtest)",
                      "trades", f"sessions {FIRST} to {LAST}", spent)
            print(f"downloaded {done} sessions, logged ${spent:.2f}")


if __name__ == "__main__":
    main()
