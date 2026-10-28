"""Crossing speed through price zones (questions 2 and 3).

Prices are cut into fixed zones (bands) of `width` ticks. Each time price
enters a zone we record how it leaves: out the far side (a crossing) or back
out the side it came in (turned back), and how long it stayed inside.

Each zone is then rated by the long-term volume profile: its volume compared
with the average zone within +/- a neighbourhood, so "low volume" means low
for that part of the chart.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def band_visits(p: np.ndarray, t: np.ndarray, width: int) -> pd.DataFrame:
    """One row per visit to a zone: band, direction (+1 entered from below), crossed, enter time, seconds inside."""
    p = np.asarray(p, dtype=np.int64)
    t = np.asarray(t, dtype=float)
    b = np.floor_divide(p, width)
    starts = np.flatnonzero(np.concatenate([[True], np.diff(b) != 0]))
    band = b[starts]
    if len(band) < 3:
        return pd.DataFrame(columns=["band", "direction", "crossed", "enter_time", "seconds"])
    r = np.arange(1, len(band) - 1)          # skip the first and last (incomplete) visits
    direction = band[r] - band[r - 1]
    crossed = (band[r + 1] - band[r]) == direction
    return pd.DataFrame({
        "band": band[r],
        "direction": direction,
        "crossed": crossed,
        "enter_time": t[starts[r]],
        "seconds": t[starts[r + 1]] - t[starts[r]],
    })


def band_relative_volume(profile: np.ndarray, tick_lo: int, width: int,
                         neighbourhood_bands: int) -> tuple[np.ndarray, np.ndarray]:
    """Rate each zone's long-term volume against nearby zones.

    profile: volume per tick starting at tick_lo. Returns (band numbers,
    relative volume): zone volume / average zone volume within
    +/- neighbourhood_bands zones. 1.0 = average for that area, 0.5 = half.
    NaN where the whole neighbourhood has no volume.
    """
    first_band = tick_lo // width
    offset = tick_lo - first_band * width
    padded = np.concatenate([np.zeros(offset), profile])
    padded = np.concatenate([padded, np.zeros((-len(padded)) % width)])
    band_vol = padded.reshape(-1, width).sum(axis=1)
    kernel = np.ones(2 * neighbourhood_bands + 1)
    present = np.convolve(np.ones(len(band_vol)), kernel, mode="same")   # fewer at the edges
    local = np.convolve(band_vol, kernel, mode="same") / present
    with np.errstate(invalid="ignore", divide="ignore"):
        rel = np.where(local > 0, band_vol / local, np.nan)
    return np.arange(first_band, first_band + len(band_vol)), rel
