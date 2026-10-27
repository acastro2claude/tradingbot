"""Download the trade data the owner approved for the step 2 backtest (27 Oct 2026).

Approved: MES trades, regular hours (09:30-16:00 ET) only, sessions 27 Apr to
23 Oct 2026 (130 sessions), each session's own front contract. Quoted $44.19,
plus up to $2 extra for retrying downloads cut off by a dropped connection
(a cut-off request may be billed, so a retry may pay for that day twice).

Prices everything first (free) and stops before downloading anything if it
would go over the cap. Every finished batch and every cut-off attempt is
logged in data-spend.md; this batch's earlier spend is read back from there.

Run from the repo root:   python -u -m research.download_backtest_trades
"""

from __future__ import annotations

import time

import requests

from research.databento_http import REPO_ROOT, SPEND_LOG, download, get_cost, log_spend
from research.trade_data_plan import request_ranges

FIRST, LAST = "2026-04-27", "2026-10-23"
APPROVED_TOTAL = 44.19
RETRY_ALLOWANCE = 2.00
MAX_ATTEMPTS = 3
OUT_DIR = REPO_ROOT / "data" / "raw" / "trades_rth"
TAG = "regular hours"          # every spend-log line of this batch contains this


def spent_on_this_batch() -> float:
    total = 0.0
    for line in SPEND_LOG.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and TAG in cells[1] and cells[4].startswith("$"):
            total += float(cells[4].lstrip("$"))
    return total


def main() -> None:
    cap = APPROVED_TOTAL + RETRY_ALLOWANCE
    reqs = request_ranges(FIRST, LAST, "rth")
    todo = [(c, a, b) for c, a, b in reqs if not (OUT_DIR / f"{c}_{a[:10]}.dbn.zst").exists()]
    quotes = [get_cost(c, "trades", a, b, "raw_symbol") for c, a, b in todo]
    already = spent_on_this_batch()
    print(f"{len(reqs)} sessions, {len(todo)} to download, quoted ${sum(quotes):.2f}; "
          f"spent so far ${already:.2f}; cap ${cap:.2f}", flush=True)
    if already + sum(quotes) > cap:
        raise SystemExit("Over the cap before starting. Nothing downloaded.")

    spent, done = 0.0, 0
    try:
        for i, ((c, a, b), q) in enumerate(zip(todo, quotes)):
            for attempt in range(1, MAX_ATTEMPTS + 1):
                still_needed = sum(quotes[i + 1:])
                if already + spent + q + still_needed > cap:
                    raise SystemExit(f"Stopping at {c} {a[:10]}: another attempt would go over the ${cap:.2f} cap.")
                try:
                    download(name=f"{c}_{a[:10]}", symbols=c, schema="trades", start=a, end=b,
                             stype_in="raw_symbol", approved_max_cost=q + 0.05, out_dir=OUT_DIR,
                             description="", log=False)
                    spent += q
                    done += 1
                    break
                except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError,
                        requests.exceptions.Timeout) as err:
                    (OUT_DIR / f"{c}_{a[:10]}.part").unlink(missing_ok=True)
                    log_spend(f"MES trades, {TAG}, session {a[:10]}: connection dropped mid-download "
                              f"(attempt {attempt}), file discarded. May have been billed.",
                              "trades", f"{a} to {b}", q)
                    already += q
                    print(f"{c} {a[:10]}: connection dropped ({type(err).__name__}), attempt {attempt}", flush=True)
                except RuntimeError as err:
                    # A gateway error (502/503/504) arrives before any data, so nothing was served or billed.
                    if not any(f"failed {code}" in str(err) for code in (502, 503, 504)):
                        raise
                    print(f"{c} {a[:10]}: gateway error, retrying in 10 s (attempt {attempt})", flush=True)
                    time.sleep(10)
            else:
                raise SystemExit(f"Gave up on {c} {a[:10]} after {MAX_ATTEMPTS} attempts.")
    finally:
        if done:
            log_spend(f"MES trades, {TAG} 09:30-16:00 ET, {done} sessions (step 2 backtest)",
                      "trades", f"sessions {FIRST} to {LAST}", spent)
        print(f"downloaded {done} sessions this run, ${spent:.2f}; batch total now "
              f"${already + spent:.2f}", flush=True)


if __name__ == "__main__":
    main()
