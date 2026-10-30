"""Supply and demand zones the way the owner's course draws them (STRATEGY.md 1b, Oct 2026).

A zone is a basing candle (where price paused) right before a strong move away. The move into
the basing candle only names the pattern:
  rally-base-rally (continuation) and drop-base-rally (reversal)  -> demand below
  drop-base-drop  (continuation) and rally-base-drop (reversal)  -> supply above

Edges, as the owner draws them (22 Aug 2024 supply 5645.50-5665.25, 15 Aug 2024 demand):
  near edge = the basing candle's open: the top of a red basing candle's body (demand),
              the bottom of a green one's (supply), so its whole body is inside the zone
  far edge  = the wick: demand bottom = lowest low of the basing and move-away candles,
              supply top = highest high of the two

Owner's rules (Oct 2026):
  - the basing candle is the candle right before the move away, red before a rally and
    green before a drop (a doji also counts, proposed)
  - the move away is roughly double the basing candle (owner); the bot uses 1.5x its range
  - not every candle that moves away makes a zone; in chop price comes straight back. The
    bot counts a zone only once price has moved departure_min_heights x the zone's height
    away from it without coming back (proposed rule); that is when the bot knows the zone
  - a zone stays valid on retests, but the more tests, the weaker: from the 4th test on it
    is probably on its way out unless the order book shows heavy pressure from its side
  - it stops counting when price blows past it (a candle on its timeframe closes beyond
    the far edge), chops in it (owner: hangs around inside for over about an hour; the
    bot scales that to chop_invalid_candles candles of the zone's timeframe, an hour on
    15-minute zones), or after about a month
  - no overlapping zones: where zones overlap, the higher timeframe wins
    (4h > 2h > 1h > 30m > 15m); on the same timeframe the earlier zone wins. A zone that
    would form over a live zone that wins is not marked. A live lower-timeframe zone stops
    counting once a higher-timeframe zone forms over it (the lower one usually appears
    first, inside the higher one's base)

A test: price trades back to the near edge. The next test needs price to move away again
by the zone's height (at least rearm_min_points) first.

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
    """Every basing candle + move away on one timeframe's candles. `z` is the [zones] settings table.

    Basing candle: the candle right before the move away, of the opposite colour (red before a
    rally, green before a drop) or a doji. Move away: range at least leg_out_vs_basing_range x
    the basing candle's and leg_out_range_vs_average x the recent average, closing beyond the
    basing candle. Zone (the owner's drawing): from the basing candle's body (its open; for a doji
    the edge price comes back to first) to the wick of the basing and move-away candles.
    """
    o, h, lo, cl = (c[k].to_numpy() for k in ("open", "high", "low", "close"))
    body = np.abs(cl - o)
    rng = h - lo
    avg = pd.Series(rng).rolling(z["average_candles"]).mean().shift(1).to_numpy()
    rows = []
    for i in range(1, len(c)):
        d = int(np.sign(cl[i] - o[i]))
        if d == 0 or not np.isfinite(avg[i]) or rng[i] < z["leg_out_range_vs_average"] * avg[i]:
            continue
        k = i - 1                                     # the basing candle
        doji = body[k] <= z["doji_body_share"] * max(rng[k], 1)
        if not (np.sign(cl[k] - o[k]) == -d or doji):
            continue                                  # no basing candle: a rally needs a red (or doji) candle before it
        if rng[i] < z["leg_out_vs_basing_range"] * max(rng[k], 1):
            continue                                  # the move away is not big enough next to the basing candle
        if (d == 1 and cl[i] <= h[k]) or (d == -1 and cl[i] >= lo[k]):
            continue                                  # the move away must close beyond the basing candle
        near = max(o[k], cl[k]) if d == 1 else min(o[k], cl[k])                   # the open, unless it is a doji
        far = min(lo[k], lo[i]) if d == 1 else max(h[k], h[i])
        k0 = max(0, k - z["leg_in_candles"])
        leg_in = cl[k - 1] - o[k0] if k > 0 else 0
        rows.append(dict(timeframe=minutes, kind="demand" if d == 1 else "supply",
                         pattern=("R" if leg_in > 0 else "D") + "B" + ("R" if d == 1 else "D"),
                         near=int(near), far=int(far), low=int(min(near, far)), high=int(max(near, far)),
                         base_candles=1, base_start=c["start"].iat[k], leg_out_start=c["start"].iat[i],
                         created=c["end"].iat[i], created_session=c["session"].iat[i],
                         leg_out_ticks=int(cl[i] - o[i])))
    return pd.DataFrame(rows, columns=COLUMNS)


def _chop_end(t: np.ndarray, inside: np.ndarray, minutes: float) -> float:
    """First time price has closed inside the zone on more than `minutes` 1-minute bars in a row.

    Counts trading minutes (bars), so a weekend or the daily halt inside the zone adds nothing.
    """
    if not inside.any():
        return np.inf
    idx = np.arange(len(inside))
    start = np.maximum.accumulate(np.where(inside, -1, idx)) + 1     # first bar of the current stay inside
    long_stay = inside & (idx - start + 1 > minutes)
    return t[np.argmax(long_stay)] + 60 if long_stay.any() else np.inf


def zone_life(zones: pd.DataFrame, bars: pd.DataFrame, candles: dict[int, pd.DataFrame], z: dict,
              tick: float) -> pd.DataFrame:
    """When the bot knows each zone, its tests, and when it stops counting. Drops zones that never left.

    Adds: formed (leg-out candle close), created (move away confirmed), tests (times price came
    back to the near edge), dies and why (broken / chopped in / expired / data ended).
    """
    t = epoch_seconds(bars["ts"])
    lo, hi, cl = (bars[k].to_numpy() for k in ("low_adj", "high_adj", "close_adj"))
    rearm_min = z["rearm_min_points"] / tick
    keep, created, tests, dies, why = [], [], [], [], []
    for n, r in enumerate(zones.itertuples(index=False)):
        d = 1 if r.kind == "demand" else -1
        height = max(r.high - r.low, 1)
        a = np.searchsorted(t, r.leg_out_start, "left")
        e = np.searchsorted(t, r.created, "left")
        end = np.searchsorted(t, r.created + z["max_age_days"] * 86400, "left")
        away = (hi[a:end] - r.near) if d == 1 else (r.near - lo[a:end])          # ticks away from the near edge
        back = (lo[a:end] <= r.near) if d == 1 else (hi[a:end] >= r.near)
        back[: e - a] = False                                                      # the leg-out candle itself
        far_enough = np.flatnonzero(away >= z["departure_min_heights"] * height)
        if not len(far_enough):
            continue
        first_back = np.flatnonzero(back)
        if len(first_back) and first_back[0] <= far_enough[0]:
            continue                                                               # came straight back: chop
        known = max(r.created, t[a + far_enough[0]] + 60)

        c = candles[r.timeframe]
        b = np.searchsorted(c["start"].to_numpy(), r.created, "left")
        cc = c["close"].to_numpy()[b:]
        out = cc < r.far if d == 1 else cc > r.far
        broken = c["end"].to_numpy()[b + np.argmax(out)] if out.any() else np.inf
        k = np.searchsorted(t, known, "left")
        inside = (cl[k:end] >= r.low) & (cl[k:end] <= r.high)
        chop = _chop_end(t[k:end], inside, z["chop_invalid_candles"] * r.timeframe)
        expiry = r.created + z["max_age_days"] * 86400 if end < len(t) else np.inf
        stop = min(broken, chop, expiry)

        hits = np.flatnonzero(back[k - a:]) + k                                    # bars back at the near edge
        rearm = np.flatnonzero(away[k - a:] >= max(height, rearm_min)) + k         # bars far enough away again
        times, j = [], 0
        while j < len(hits) and t[hits[j]] < stop:
            times.append(float(t[hits[j]]))
            j2 = np.searchsorted(rearm, hits[j], "right")                          # away again, after this test
            if j2 == len(rearm):
                break
            j = np.searchsorted(hits, rearm[j2], "right")                          # then back again
        keep.append(n)
        created.append(known)
        tests.append(times)
        dies.append(stop)
        why.append("data ended" if stop == np.inf else
                   "broken" if stop == broken else "chopped in" if stop == chop else "expired")
    out = zones.iloc[keep].copy()
    out["formed"] = out["created"]
    out["created"] = created
    out["tests"] = tests
    out["dies"] = dies
    out["why"] = why
    return out.reset_index(drop=True)


def resolve_overlaps(zones: pd.DataFrame) -> pd.DataFrame:
    """Keep one zone per price area: the higher timeframe, then the earlier zone.

    Needs created/dies (zone_life). Two zones overlap when their price ranges share a tick
    while both count. Tests after a zone was replaced are dropped. Adds n_tests and first_touch.
    """
    z = zones.sort_values(["timeframe", "created"], ascending=[False, True]).reset_index(drop=True)
    lo, hi = z["low"].to_numpy(), z["high"].to_numpy()
    tf, cr = z["timeframe"].to_numpy(), z["created"].to_numpy()
    dies = z["dies"].to_numpy().copy()
    why = z["why"].to_numpy().copy()
    keep = np.zeros(len(z), bool)
    for i in range(len(z)):
        prev = np.nonzero(keep[:i])[0]                     # kept so far: higher timeframes, or same and earlier
        prev = prev[(lo[prev] <= hi[i]) & (hi[prev] >= lo[i])]
        if ((cr[prev] <= cr[i]) & (dies[prev] > cr[i])).any():
            continue                                       # forms over a live zone that counts more
        later = prev[(tf[prev] > tf[i]) & (cr[prev] > cr[i]) & (cr[prev] < dies[i])]
        if len(later):
            dies[i] = cr[later].min()                      # a higher-timeframe zone formed over it
            why[i] = "replaced by a higher timeframe"
        keep[i] = True
    z["dies"] = dies
    z["why"] = why
    z["tests"] = [[x for x in ts if x < d] for ts, d in zip(z["tests"], dies)]
    z["n_tests"] = z["tests"].map(len)
    z["first_touch"] = z["tests"].map(lambda ts: ts[0] if ts else np.inf)
    return z[keep].sort_values("created").reset_index(drop=True)


def all_zones(bars: pd.DataFrame, s: dict) -> tuple[pd.DataFrame, dict[int, pd.DataFrame]]:
    """Zones on every timeframe in [zones] timeframes_minutes: found, confirmed, tested, overlaps resolved."""
    z = s["zones"]
    candles = {m: make_candles(bars, m, z["candle_clock_tz"]) for m in z["timeframes_minutes"]}
    found = pd.concat([find_zones(candles[m], m, z) for m in z["timeframes_minutes"]], ignore_index=True)
    return resolve_overlaps(zone_life(found, bars, candles, z, s["market"]["tick_size"])), candles
