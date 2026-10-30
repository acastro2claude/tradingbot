"""Download the ES full order book (MBO) for the five weeks the owner approved (28 Oct 2026).

Approved: whole regular sessions for the weeks in
research/results/journal/es_week_price_plan.csv (Feb 26, Apr 8, Jun 3, Jul 29 and
Sep 23 2024), quoted $27.53. Each day runs from 00:00 UTC (Databento's daily
full-book snapshot) to 16:00 ET, on that session's front ES contract.

Prices everything first and stops if the total is over the approved amount
(+ $0.25 for quotes moving by cents), counting this batch's earlier runs.
Gateway errors (no data sent, not billed) are retried. A dropped connection
mid-download may be billed: it is logged as "possibly billed" and retried only
if the total stays within the cap. One line per run goes into data-spend.md.

Run from the repo root:
    python -u -m research.download_es_weeks --only 2024-02-26   (single-day test)
    python -u -m research.download_es_weeks                     (everything else)
"""

from __future__ import annotations

import argparse
import time

import pandas as pd
import requests

from research.databento_http import REPO_ROOT, SPEND_LOG, download, log_spend
from research.es_orderbook_price_plan import quote
from research.journal_study import OUT

APPROVED_TOTAL = 27.53
TOLERANCE = 0.25
OUT_DIR = REPO_ROOT / "data" / "raw" / "es_weeks_mbo"
TAG = "ES order book, 5 weeks"


def already_spent() -> float:
    total = 0.0
    for line in SPEND_LOG.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and TAG in cells[1] and cells[4].startswith("$"):
            total += float(cells[4].lstrip("$"))
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="download just this day (YYYY-MM-DD)")
    ap.add_argument("--skip", nargs="*", default=[], help="days to leave out (YYYY-MM-DD)")
    args = ap.parse_args()
    plan = pd.read_csv(OUT / "es_week_price_plan.csv")
    if args.only:
        plan = plan[plan["day"] == args.only]
    plan = plan[~plan["day"].isin(args.skip)]
    todo = [r for r in plan.itertuples() if not (OUT_DIR / f"{r.es}_{r.day}_mbo.dbn.zst").exists()]
    quotes = [quote(r.es, "mbo", r.mbo_start, r.rth_end) for r in todo]     # retries gateway errors
    cap = APPROVED_TOTAL + TOLERANCE - already_spent()
    print(f"{len(todo)} days to download, quoted ${sum(quotes):.2f}, cap left ${cap:.2f}", flush=True)
    if sum(quotes) > cap:
        raise SystemExit("Over the approved amount. Nothing downloaded.")
    spent, done, maybe = 0.0, 0, 0.0
    try:
        for i, (r, q) in enumerate(zip(todo, quotes)):
            for attempt in range(1, 4):
                if spent + maybe + q + sum(quotes[i + 1:]) > cap:
                    raise SystemExit(f"Stopping at {r.es} {r.day}: another attempt would pass the ${cap:.2f} cap.")
                try:
                    download(name=f"{r.es}_{r.day}_mbo", symbols=r.es, schema="mbo", start=r.mbo_start,
                             end=r.rth_end, stype_in="raw_symbol", approved_max_cost=q + 0.05,
                             out_dir=OUT_DIR, description="", log=False)
                    spent += q
                    done += 1
                    break
                except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError,
                        requests.exceptions.Timeout):
                    (OUT_DIR / f"{r.es}_{r.day}_mbo.dbn.part").unlink(missing_ok=True)
                    log_spend(f"{TAG}, {r.day}: connection dropped mid-download, file discarded. "
                              "May have been billed.", "mbo", f"{r.mbo_start} to {r.rth_end}", q)
                    maybe += q
                except RuntimeError as err:
                    if not any(f"failed {c}" in str(err) for c in (502, 503, 504)):
                        raise
                    time.sleep(10)
                except requests.exceptions.HTTPError as err:    # gateway error on the fresh price check
                    if err.response is None or err.response.status_code not in (502, 503, 504):
                        raise
                    print(f"{r.day}: gateway error on the price check, retrying", flush=True)
                    time.sleep(10)
            else:
                raise SystemExit(f"Gave up on {r.es} {r.day} after 3 attempts.")
    finally:
        if done:
            days = todo[0].day if done == 1 else f"{done} days"
            log_spend(f"{TAG}: {days} (whole regular sessions, bot vs owner comparison)",
                      "mbo", "00:00 UTC to 16:00 ET, each day", spent)
        print(f"downloaded {done} days, ${spent:.2f} (+${maybe:.2f} possibly billed)", flush=True)


if __name__ == "__main__":
    main()
