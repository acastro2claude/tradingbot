"""Download the MES full order book (MBO) the owner approved for 2026 replay clips (Nov 2026).

Approved: the 16 days picked in research/results/journal/replay2026_plan.csv (pick = True),
quoted $19.47. Each day runs from 00:00 UTC (Databento's daily full-book snapshot, so the whole
book can be rebuilt) to 16:00 ET.

Prices everything first and stops if the total is over the approved amount (+ $0.25 for quotes
moving by cents). Gateway errors (no data sent, not billed) are retried. A dropped connection
mid-download may be billed: it is logged as "possibly billed" and retried only if the total stays
within the cap. One line per run goes into data-spend.md.

Run from the repo root:
    python -u -m research.download_2026_orderbook --only 2026-07-21   (single-day test)
    python -u -m research.download_2026_orderbook                     (all 16 days)
"""

from __future__ import annotations

import argparse
import time

import pandas as pd
import requests

from research.databento_http import REPO_ROOT, SPEND_LOG, download, get_cost, log_spend
from research.journal_study import OUT

APPROVED_TOTAL = 19.47
TOLERANCE = 0.25
OUT_DIR = REPO_ROOT / "data" / "raw" / "mes2026_mbo"
TAG = "2026 replay order book"


def already_spent() -> float:
    total = 0.0
    for line in SPEND_LOG.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and TAG in cells[1] and cells[4].startswith("$"):
            total += float(cells[4].lstrip("$"))
    return total


def plan_rows(only: str | None) -> list:
    plan = pd.read_csv(OUT / "replay2026_plan.csv")
    plan = plan[plan["pick"]]
    if only:
        plan = plan[plan["day"] == only]
    return [r for r in plan.itertuples() if not (OUT_DIR / f"{r.contract}_{r.day}_mbo.dbn.zst").exists()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="download just this day (YYYY-MM-DD)")
    args = ap.parse_args()
    todo = plan_rows(args.only)
    quotes = [get_cost(r.contract, "mbo", r.book_start, r.book_end, "raw_symbol") for r in todo]
    cap = APPROVED_TOTAL + TOLERANCE - already_spent()
    print(f"{len(todo)} days to download, quoted ${sum(quotes):.2f}, cap left ${cap:.2f}", flush=True)
    if sum(quotes) > cap:
        raise SystemExit("Over the approved amount. Nothing downloaded.")
    spent, done, maybe = 0.0, 0, 0.0
    try:
        for i, (r, q) in enumerate(zip(todo, quotes)):
            for attempt in range(1, 4):
                if spent + maybe + q + sum(quotes[i + 1:]) > cap:
                    raise SystemExit(f"Stopping at {r.contract} {r.day}: another attempt would pass the ${cap:.2f} cap.")
                try:
                    download(name=f"{r.contract}_{r.day}_mbo", symbols=r.contract, schema="mbo", start=r.book_start,
                             end=r.book_end, stype_in="raw_symbol", approved_max_cost=q + 0.05,
                             out_dir=OUT_DIR, description="", log=False)
                    spent += q
                    done += 1
                    break
                except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError,
                        requests.exceptions.Timeout):
                    (OUT_DIR / f"{r.contract}_{r.day}_mbo.part").unlink(missing_ok=True)
                    log_spend(f"MES full order book, {TAG}, {r.day}: connection dropped mid-download, "
                              "file discarded. May have been billed.", "mbo", f"{r.book_start} to {r.book_end}", q)
                    maybe += q
                except RuntimeError as err:
                    if not any(f"failed {c}" in str(err) for c in (502, 503, 504)):
                        raise
                    time.sleep(10)
            else:
                raise SystemExit(f"Gave up on {r.contract} {r.day} after 3 attempts.")
    finally:
        if done:
            days = todo[0].day if done == 1 else f"{done} days"
            log_spend(f"MES full order book, {TAG}: {days} (zone tests for the owner to mark)",
                      "mbo", "00:00 UTC to 16:00 ET, each day", spent)
        print(f"downloaded {done} days, ${spent:.2f} (+${maybe:.2f} possibly billed)", flush=True)


if __name__ == "__main__":
    main()
