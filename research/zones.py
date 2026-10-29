"""Supply and demand zones the way the owner's course draws them (STRATEGY.md 1b, Oct 2026).

A zone is the base (1 to 3 candles where price paused) right before a strong move away
(the leg-out). The move into the base only names the pattern:
  rally-base-rally (continuation) and drop-base-rally (reversal)  -> demand below
  drop-base-drop  (continuation) and rally-base-drop (reversal)  -> supply above

Edges (as in the course charts and the owner's 14-15 Aug 2024 example):
  near edge = the base candles' bodies: demand top = highest open/close in the base,
              supply bottom = lowest open/close in the base
  far edge  = the base candles' wicks: demand bottom = lowest low in the base,
              supply top = highest high in the base (not the leg-out candle's wick, which
              on a news candle can be huge)

A zone is fresh until price first trades back to its near edge (the first return is the
trade). It is broken when a candle on its own timeframe closes beyond the far edge, and
expires if price has not come back within max_age_days (owner: about a month).

No overlapping zones (owner, Oct 2026): where zones overlap, the higher timeframe wins
(4h > 2h > 1h > 30m > 15m). A zone that would form over a fresh zone of a higher (or the
same) timeframe is not marked. A fresh lower-timeframe zone stops counting once a
higher-timeframe zone forms over it (the lower one usually appears first, inside the
higher one's base).

Candles are aligned like the owner's thinkorswim charts: buckets from midnight Chicago
time (a 4-hour chart has candles at 00, 04, 08, 12, 16 (17:00 open) and 20 CT), and a
candle never spans two sessions. Prices are adjusted ticks (research.market_data), times
epoch seconds. All numbers come from [zones] in settings.toml.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.market_data import epoch_seconds

COLUMNS = ["timeframe", "kind", "pattern", "near", "far", "low", "high", "base_candles", "base_start",
           "leg_out_start", "created", "created_session", "leg_out_ticks"]


def make_candles(bars: pd.DataFrame, minutes: int, tz: str) -> pd.DataFrame:
    """Candles from 1-minute bars (ts, session, *_adj, volume), bucketed on the local clock in `tz`."""
    local = bars["ts"].dt.tz_convert(tz).dt.tz_localize(None)
    bucket = local.dt.floor(f"{minutes}min")
    g = bars.assign(_bucket=bucket.to_numpy(), _local=local.to_numpy()).groupby(["session", "_bucket"], sort=True)
    c = g.agg(open=("open_adj", "first"), high=("high_adj", "max"), low=("low_adj", "min"),
              close=("close_adj", "last"), volume=("volume", "sum"), first_ts=("ts", "min"),
              first_local=("_local", "min"), last_ts=("ts", "max")).reset_index()
    # nominal start: the first bar's time minus how far it sits into its bucket
    into = (c["first_local"] - c["_bucket"]).dt.total_seconds().to_numpy()
    c["start"] = epoch_seconds(c["first_ts"]) - into
    c["end"] = np.minimum(c["start"] + minutes * 60, epoch_seconds(c["last_ts"]) + 60)
    c["label"] = c["_bucket"]
    return c.drop(columns=["_bucket", "first_ts", "first_local", "last_ts"]).sort_values("start").reset_index(drop=True)


def find_zones(c: pd.DataFrame, minutes: int, z: dict) -> pd.DataFrame:
    """Every base + leg-out on one timeframe's candles. `z` is the [zones] settings table."""
    o, h, lo, cl = (c[k].to_numpy() for k in ("open", "high", "low", "close"))
    body = np.abs(cl - o)
    rng = h - lo
    avg = pd.Series(rng).rolling(z["average_candles"]).mean().shift(1).to_numpy()
    rows = []
    for i in range(1, len(c)):
        d = int(np.sign(cl[i] - o[i]))
        if d == 0 or not np.isfinite(avg[i]) or rng[i] < z["leg_out_range_vs_average"] * avg[i]:
            continue
        top, bottom, j = np.inf, -np.inf, i          # base: walk back over small candles sharing a price
        while j > 0 and i - (j - 1) <= z["base_max_candles"]:
            k = j - 1
            if body[k] * z["leg_out_body_ratio"] > body[i]:
                break
            nt, nb = min(top, h[k]), max(bottom, lo[k])
            if nb > nt:
                break
            top, bottom, j = nt, nb, k
        if j == i:
            continue
        base_hi, base_lo = h[j:i].max(), lo[j:i].min()
        if (d == 1 and cl[i] <= base_hi) or (d == -1 and cl[i] >= base_lo):
            continue                                  # the leg-out must close beyond the whole base
        if d == 1:
            near, far = np.maximum(o[j:i], cl[j:i]).max(), base_lo
        else:
            near, far = np.minimum(o[j:i], cl[j:i]).min(), base_hi
        k0 = max(0, j - z["leg_in_candles"])
        leg_in = cl[j - 1] - o[k0] if j > 0 else 0
        rows.append(dict(timeframe=minutes, kind="demand" if d == 1 else "supply",
                         pattern=("R" if leg_in > 0 else "D") + "B" + ("R" if d == 1 else "D"),
                         near=int(near), far=int(far), low=int(min(near, far)), high=int(max(near, far)),
                         base_candles=i - j, base_start=c["start"].iat[j], leg_out_start=c["start"].iat[i],
                         created=c["end"].iat[i], created_session=c["session"].iat[i],
                         leg_out_ticks=int(cl[i] - o[i])))
    return pd.DataFrame(rows, columns=COLUMNS)


def zone_status(zones: pd.DataFrame, bars: pd.DataFrame, candles: dict[int, pd.DataFrame],
                max_age_days: float) -> pd.DataFrame:
    """Add first_touch (first 1-minute bar back at the near edge), broken (close beyond the far edge),
    expires (created + max_age_days) and fresh_until (the first of those three)."""
    z = zones.copy()
    t = epoch_seconds(bars["ts"])
    lo, hi = bars["low_adj"].to_numpy(), bars["high_adj"].to_numpy()
    first, broken = [], []
    for r in z.itertuples(index=False):
        a = np.searchsorted(t, r.created, "left")
        hit = lo[a:] <= r.near if r.kind == "demand" else hi[a:] >= r.near
        first.append(t[a + np.argmax(hit)] if hit.any() else np.inf)
        c = candles[r.timeframe]
        b = np.searchsorted(c["start"].to_numpy(), r.created, "left")
        cc = c["close"].to_numpy()[b:]
        out = cc < r.far if r.kind == "demand" else cc > r.far
        broken.append(c["end"].to_numpy()[b + np.argmax(out)] if out.any() else np.inf)
    z["first_touch"] = first
    z["broken"] = broken
    z["expires"] = z["created"] + max_age_days * 86400
    z["fresh_until"] = z[["first_touch", "broken", "expires"]].min(axis=1)
    return z


def resolve_overlaps(zones: pd.DataFrame) -> pd.DataFrame:
    """Keep one zone per price area: the higher timeframe, then the earlier zone. Adds `ended`.

    Needs fresh_until (zone_status). Two zones overlap when their price ranges share a tick
    while both are fresh.
    """
    z = zones.sort_values(["timeframe", "created"], ascending=[False, True]).reset_index(drop=True)
    lo, hi = z["low"].to_numpy(), z["high"].to_numpy()
    tf, cr = z["timeframe"].to_numpy(), z["created"].to_numpy()
    fu = z["fresh_until"].to_numpy().copy()
    keep = np.zeros(len(z), bool)
    replaced = np.full(len(z), np.inf)
    for i in range(len(z)):
        prev = np.nonzero(keep[:i])[0]                     # kept so far: higher timeframes, or same and earlier
        prev = prev[(lo[prev] <= hi[i]) & (hi[prev] >= lo[i])]
        if ((cr[prev] <= cr[i]) & (fu[prev] > cr[i])).any():
            continue                                       # forms over a fresh zone that counts more
        later = prev[(tf[prev] > tf[i]) & (cr[prev] > cr[i]) & (cr[prev] < fu[i])]
        if len(later):
            replaced[i] = fu[i] = cr[later].min()          # a higher-timeframe zone formed over it
        keep[i] = True
    z["fresh_until"] = fu
    z["replaced"] = replaced
    z["ended"] = np.select([fu == replaced, fu == z["first_touch"], fu == z["broken"], fu == z["expires"]],
                           ["replaced by a higher timeframe", "tested", "broken", "expired"], "data ended")
    return z[keep].sort_values("created").reset_index(drop=True)


def all_zones(bars: pd.DataFrame, s: dict) -> tuple[pd.DataFrame, dict[int, pd.DataFrame]]:
    """Zones on every timeframe in [zones] timeframes_minutes, overlaps resolved, with first touch and break times."""
    z = s["zones"]
    candles = {m: make_candles(bars, m, z["candle_clock_tz"]) for m in z["timeframes_minutes"]}
    found = pd.concat([find_zones(candles[m], m, z) for m in z["timeframes_minutes"]], ignore_index=True)
    return resolve_overlaps(zone_status(found, bars, candles, z["max_age_days"])), candles
