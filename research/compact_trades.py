"""Save compact summaries of the raw trade files so they never need re-buying.

The sandbox is wiped between sessions and raw data can't be committed, so
for each regular-hours session this keeps (in the contract's own prices):
  - 1-second bars: open, high, low, close, buy volume, sell volume, trades
  - 1-minute footprint: volume at each price, split into aggressive buys and sells
Bursts of 50+ contracts (absorption) are saved by research.absorption_study.

Run from the repo root:   python -m research.compact_trades
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from research.absorption_study import session_files
from research.market_data import epoch_seconds, load_trades
from research.settings import load_settings

COMPACT = Path(__file__).resolve().parent / "results" / "step2" / "compact"


def one_session(f: Path, s: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    tr = load_trades(f, s)
    sec = np.floor(epoch_seconds(tr["ts"])).astype(np.int64)
    buy = np.where(tr["side"] == "B", tr["size"], 0)
    sell = np.where(tr["side"] == "A", tr["size"], 0)
    df = pd.DataFrame({"sec": sec, "price": tr["price"].astype(np.int32), "buy": buy, "sell": sell})
    g = df.groupby("sec", sort=True)
    bars = pd.DataFrame({
        "open": g["price"].first(), "high": g["price"].max(), "low": g["price"].min(),
        "close": g["price"].last(), "buy": g["buy"].sum(), "sell": g["sell"].sum(),
        "trades": g["price"].size(),
    }).reset_index()
    df["minute"] = df["sec"] // 60 * 60
    fp = df.groupby(["minute", "price"], sort=True)[["buy", "sell"]].sum().reset_index()
    for d in (bars, fp):
        for c in d.columns:
            if c not in ("sec", "minute"):
                d[c] = d[c].astype(np.int32)
    return bars, fp


def main() -> None:
    s = load_settings()
    COMPACT.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list] = {}
    for sess, contract, f in session_files():
        bars, fp = one_session(f, s)
        bars.insert(0, "session", sess)
        bars.insert(1, "contract", contract)
        fp.insert(0, "session", sess)
        fp.insert(1, "contract", contract)
        by_month.setdefault(sess.strftime("%Y-%m"), []).append((bars, fp))
    for month, parts in by_month.items():
        pd.concat([b for b, _ in parts]).to_parquet(COMPACT / f"{month}_1s_bars.parquet",
                                                    index=False, compression="zstd")
        pd.concat([f for _, f in parts]).to_parquet(COMPACT / f"{month}_footprint_1min.parquet",
                                                     index=False, compression="zstd")
    sizes = sum(p.stat().st_size for p in COMPACT.glob("*.parquet")) / 1e6
    print(f"saved {len(by_month)} months to {COMPACT}, {sizes:.1f} MB")


if __name__ == "__main__":
    main()
