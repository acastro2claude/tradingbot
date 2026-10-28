"""Download the full MES order book (MBO) the owner approved for the journal days (28 Oct 2026).

Approved: full book for the 20 busiest journal days in
research/results/journal/orderbook_price_plan.csv, quoted $8.35. Each day runs
from 00:00 UTC (Databento's daily full-book snapshot, so the whole book can be
rebuilt) to 15 minutes after the owner's last exit that day.

Prices everything first and stops if the total is over the approved amount
(+ $0.25 for quotes moving by cents). Gateway errors (no data sent, not billed)
are retried. A dropped connection mid-download may be billed: it is logged as
"possibly billed" and retried only if the total stays within the cap.
One line per run goes into data-spend.md.

Run from the repo root:
    python -u -m research.download_journal_orderbook --only 2024-03-25   (single-day test)
    python -u -m research.download_journal_orderbook                     (all 20 days)
"""

from __future__ import annotations

import argparse
import time

import pandas as pd
import requests

from research.databento_http import REPO_ROOT, SPEND_LOG, download, get_cost, log_spend
from research.journal_study import OUT

APPROVED_TOTAL = 8.35
TOLERANCE = 0.25
DAYS = 20
OUT_DIR = REPO_ROOT / "data" / "raw" / "journal_mbo"
TAG = "journal order book"


def already_spent() -> float:
    """This batch's earlier spend, read back from data-spend.md."""
    total = 0.0
    for line in SPEND_LOG.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and TAG in cells[1] and cells[4].startswith("$"):
            total += float(cells[4].lstrip("$"))
    return total


def plan_rows(only: str | None) -> list:
    plan = pd.read_csv(OUT / "orderbook_price_plan.csv").head(DAYS)
    if only:
        plan = plan[plan["day"] == only]
    return [r for r in plan.itertuples() if not (OUT_DIR / f"{r.mes}_{r.day}_mbo.dbn.zst").exists()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="download just this day (YYYY-MM-DD)")
    args = ap.parse_args()
    todo = plan_rows(args.only)
    quotes = [get_cost(r.mes, "mbo", r.day_start, r.end, "raw_symbol") for r in todo]
    cap = APPROVED_TOTAL + TOLERANCE - already_spent()
    print(f"{len(todo)} days to download, quoted ${sum(quotes):.2f}, cap left ${cap:.2f}", flush=True)
    if sum(quotes) > cap:
        raise SystemExit("Over the approved amount. Nothing downloaded.")
    spent, done, maybe = 0.0, 0, 0.0
    try:
        for i, (r, q) in enumerate(zip(todo, quotes)):
            for attempt in range(1, 4):
                if spent + maybe + q + sum(quotes[i + 1:]) > cap:
                    raise SystemExit(f"Stopping at {r.mes} {r.day}: another attempt would pass the ${cap:.2f} cap.")
                try:
                    download(name=f"{r.mes}_{r.day}_mbo", symbols=r.mes, schema="mbo", start=r.day_start,
                             end=r.end, stype_in="raw_symbol", approved_max_cost=q + 0.05,
                             out_dir=OUT_DIR, description="", log=False)
                    spent += q
                    done += 1
                    break
                except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError,
                        requests.exceptions.Timeout):
                    (OUT_DIR / f"{r.mes}_{r.day}_mbo.part").unlink(missing_ok=True)
                    log_spend(f"MES full order book, {TAG}, {r.day}: connection dropped mid-download, "
                              "file discarded. May have been billed.", "mbo", f"{r.day_start} to {r.end}", q)
                    maybe += q
                except RuntimeError as err:
                    if not any(f"failed {c}" in str(err) for c in (502, 503, 504)):
                        raise
                    time.sleep(10)
            else:
                raise SystemExit(f"Gave up on {r.mes} {r.day} after 3 attempts.")
    finally:
        if done:
            days = todo[0].day if done == 1 else f"{done} days"
            log_spend(f"MES full order book, {TAG}: {days} (owner's busiest 2024-2025 journal days)",
                      "mbo", "00:00 UTC to 15 min after the last exit, each day", spent)
        print(f"downloaded {done} days, ${spent:.2f} (+${maybe:.2f} possibly billed)", flush=True)


if __name__ == "__main__":
    main()
