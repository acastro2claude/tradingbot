"""Download the data the owner approved for replaying the journal (28 Oct 2026).

Approved: MES trades for the 151 day windows in
research/results/journal/replay_price_plan.csv (15 minutes before each day's
first journal entry to 15 minutes after its last exit), quoted $10.80.

Prices everything first and stops if the total is over the approved amount
(+ $0.25 for quotes moving by cents). Gateway errors (no data sent, not billed)
are retried. A dropped connection mid-download may be billed: it is logged as
"possibly billed" and retried only if the total stays within the cap.
One line per run goes into data-spend.md.

Run from the repo root:   python -u -m research.download_journal_replay
"""

from __future__ import annotations

import time

import pandas as pd
import requests

from research.databento_http import REPO_ROOT, download, get_cost, log_spend
from research.journal_study import OUT

APPROVED_TOTAL = 10.80
TOLERANCE = 0.25
OUT_DIR = REPO_ROOT / "data" / "raw" / "journal_mes"
TAG = "journal replay"


def main() -> None:
    plan = pd.read_csv(OUT / "replay_price_plan.csv")
    todo = [r for r in plan.itertuples() if not (OUT_DIR / f"{r.mes}_{r.day}.dbn.zst").exists()]
    quotes = [get_cost(r.mes, "trades", r.start, r.end, "raw_symbol") for r in todo]
    cap = APPROVED_TOTAL + TOLERANCE
    print(f"{len(plan)} windows, {len(todo)} to download, quoted ${sum(quotes):.2f}, cap ${cap:.2f}", flush=True)
    if sum(quotes) > cap:
        raise SystemExit("Over the approved amount. Nothing downloaded.")
    spent, done, maybe = 0.0, 0, 0.0
    try:
        for i, (r, q) in enumerate(zip(todo, quotes)):
            for attempt in range(1, 4):
                if spent + maybe + q + sum(quotes[i + 1:]) > cap:
                    raise SystemExit(f"Stopping at {r.mes} {r.day}: another attempt would pass the ${cap:.2f} cap.")
                try:
                    download(name=f"{r.mes}_{r.day}", symbols=r.mes, schema="trades", start=r.start,
                             end=r.end, stype_in="raw_symbol", approved_max_cost=q + 0.05,
                             out_dir=OUT_DIR, description="", log=False)
                    spent += q
                    done += 1
                    break
                except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError,
                        requests.exceptions.Timeout):
                    (OUT_DIR / f"{r.mes}_{r.day}.part").unlink(missing_ok=True)
                    log_spend(f"MES trades, {TAG}, {r.day}: connection dropped mid-download, "
                              "file discarded. May have been billed.", "trades", f"{r.start} to {r.end}", q)
                    maybe += q
                except RuntimeError as err:
                    if not any(f"failed {c}" in str(err) for c in (502, 503, 504)):
                        raise
                    time.sleep(10)
            else:
                raise SystemExit(f"Gave up on {r.mes} {r.day} after 3 attempts.")
    finally:
        if done:
            log_spend(f"MES trades, {TAG}: {done} day windows around the owner's 2024-2025 trades",
                      "trades", "15 min before first entry to 15 min after last exit, each day", spent)
        print(f"downloaded {done} windows, ${spent:.2f} (+${maybe:.2f} possibly billed)", flush=True)


if __name__ == "__main__":
    main()
