"""Absorption bursts, rebuilt from trades the way the Bookmap Absorption Indicator works.

Bookmap's description: a mark appears "when a price level's traded volume
within a configurable period exceeds a configurable size. Buys and sells are
calculated separately." The owner's setting on MES: 100 contracts in 0.01 s.

So a burst = trades at ONE price, by ONE aggressor side, starting with a trade
and including every trade at that price and side within `window` seconds of it.
Each burst is its own marker (owner: markers at one price don't add up).

Colours (settled with Example 1, 3 Apr 2025):
  aggressor = buyers  -> sellers absorbed them -> purple, "seller absorption" (bearish)
  aggressor = sellers -> buyers absorbed them  -> green,  "buyer absorption"  (bullish)

Databento trade sides: "B" = buy aggressor, "A" = sell aggressor.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def find_bursts(t: np.ndarray, price: np.ndarray, size: np.ndarray, side: np.ndarray,
                window: float, min_size: int) -> pd.DataFrame:
    """Bursts of at least `min_size` contracts at one price and side within `window` seconds.

    t: trade times in seconds (sorted); price: ticks; size: contracts; side: "A"/"B".
    Returns time, end_time, side, price, contracts, trades, colour.
    """
    df = pd.DataFrame({"t": t, "price": price, "size": size, "side": side})
    df = df[df["side"].isin(["A", "B"])]
    rows = []
    for (sd, px), g in df.groupby(["side", "price"], sort=False):
        tt = g["t"].to_numpy()
        ss = g["size"].to_numpy()
        if ss.sum() < min_size:
            continue
        # anchored windows: a burst starts at a trade and takes everything within `window` of it
        start = 0
        n = len(tt)
        while start < n:
            end = np.searchsorted(tt, tt[start] + window, side="right")
            total = ss[start:end].sum()
            if total >= min_size:
                rows.append((tt[start], tt[end - 1], sd, px, int(total), end - start))
            start = end
    out = pd.DataFrame(rows, columns=["time", "end_time", "side", "price", "contracts", "trades"])
    out["colour"] = np.where(out["side"] == "B", "purple", "green")
    return out.sort_values(["time", "price"]).reset_index(drop=True)
