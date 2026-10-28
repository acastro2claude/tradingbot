"""Replay the owner's journal trades against MES trade data and ES bars.

For every journal trade (with corrected times) this measures what the market
showed just before the entry, and compares it with random moments in the
same time windows (both directions):
  - absorption: bursts (one price, one aggressor side, within 0.01 s) of the
    colour that fits the trade (purple before a short, green before a long),
    within 2 points of the entry in the 5 minutes before it, at 100+ and 50+
  - order flow: delta over the last 1 and 5 minutes, in the trade's direction
  - the move into the entry: how far price came toward the entry over 5 and 15 minutes
  - levels: distance to the owner's recent uncovered POCs, POC clusters, and
    15-minute supply/demand zones of the matching kind (from ES 1-minute bars)
  - the trade itself: the furthest it went for and against the owner while open

Needs data/raw/journal_mes (research.download_journal_replay) and the ES bars
(research.download_journal_bars).
Run from the repo root:   python -m research.journal_replay
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from research.absorption import find_bursts
from research.journal_study import ES_BARS, ES_SYMBOLOGY, OUT
from research.level_study import RAW, prepare
from research.levels import cluster_pocs
from research.market_data import epoch_seconds, load_trades, session_dates
from research.paths import trade_path
from research.recent_poc_study import recent_poc_labels
from research.settings import load_settings, ticks
from research.supply_demand import find_zones, make_candles, zone_lifetimes

MES_DIR = RAW / "journal_mes"
CONDITION_ES = RAW / "GLBX_condition_2024.json"
NEAR_POINTS = 2
LOOKBACK_S = 300


def es_context(s: dict):
    """ES sessions (POCs, value areas, roll offsets) and 15-minute zones for the journal period."""
    fb, sessions, _ = prepare(s, ES_BARS, ES_SYMBOLOGY, ("2024-01-16", "2025-03-21"), CONDITION_ES)
    bot = s["bot"]
    candles = make_candles(fb, 15)
    zones = find_zones(candles, bot["sd_base_min_candles"], bot["sd_departure_min_ratio"], bot["sd_zone_drawing"])
    zones = zone_lifetimes(zones, candles, fb, bot["sd_chop_invalid_minutes"],
                           bot["sd_lookback_months"] * s["level_study"]["sessions_per_month"], list(sessions.index))
    offsets = sessions.groupby("contract")["offset_ticks"].first()
    return sessions, zones, offsets


def features(at_t: np.ndarray, at_px: np.ndarray, d: np.ndarray, m: dict, tol: int) -> pd.DataFrame:
    """Market features before each moment (time s, price ticks, direction) from one day's MES data."""
    b, tt, px, signed, cvd = m["bursts"], m["tt"], m["px"], m["signed"], m["cvd"]
    rows = []
    for t0, e, dd in zip(at_t, at_px, d):
        w = b[(b["end_time"] <= t0) & (b["end_time"] > t0 - LOOKBACK_S) & ((b["price"] - e).abs() <= tol)]
        mine, theirs = w[w["direction"] == dd], w[w["direction"] == -dd]
        i0 = np.searchsorted(tt, t0, "right")
        i1 = np.searchsorted(tt, t0 - 60, "left")
        i5 = np.searchsorted(tt, t0 - 300, "left")
        i15 = np.searchsorted(tt, t0 - 900, "left")
        p_now = px[i0 - 1] if i0 > 0 else e
        rows.append(dict(
            match_100=int((mine["contracts"] >= 100).sum()), opp_100=int((theirs["contracts"] >= 100).sum()),
            match_50=len(mine), opp_50=len(theirs),
            match_contracts=int(mine["contracts"].sum()), opp_contracts=int(theirs["contracts"].sum()),
            delta_1m=int(signed[i1:i0].sum()) * dd, delta_5m=int(signed[i5:i0].sum()) * dd,
            move_5m=(p_now - px[i5]) * -dd if i5 < i0 else 0,     # how far price came toward the entry
            move_15m=(p_now - px[i15]) * -dd if i15 < i0 else 0,
            cvd_slope_5m=(cvd[i0 - 1] - cvd[i5]) * dd if i5 < i0 else 0))
    return pd.DataFrame(rows)


def load_day(path: Path, s: dict) -> dict:
    tr = load_trades(path, s)
    tt = epoch_seconds(tr["ts"])
    px = tr["price"].to_numpy().astype(np.int64)
    size = tr["size"].to_numpy().astype(np.int64)
    side = tr["side"].to_numpy()
    signed = np.where(side == "B", size, np.where(side == "A", -size, 0))
    b = find_bursts(tt, px, size, side, s["bot"]["absorption_burst_seconds"], 50)
    b["direction"] = np.where(b["colour"] == "purple", -1, 1)
    p, t = trade_path(px, tt)
    return dict(tt=tt, px=px, signed=signed, cvd=np.cumsum(signed), bursts=b, p=p, t=t)


def level_distances(sessions, zones, offsets, session, contract, price, d, when, s) -> dict:
    """Ticks from the price to the nearest owner's level, cluster and matching zone (NaN if none)."""
    out = dict(poc_dist=np.nan, cluster_dist=np.nan, zone_dist=np.nan)
    if session not in sessions.index or not sessions.loc[session, "use"] or contract not in offsets.index:
        return out
    off = int(offsets[contract])
    usable = list(sessions.index[sessions["use"]])
    k = usable.index(session)
    bot = s["bot"]
    if k >= bot["recent_poc_lookback_sessions"]:
        rec = recent_poc_labels(sessions, pd.Index([session]), bot["recent_poc_lookback_sessions"])
        pocs = rec.loc[~rec["covered"], "price"].to_numpy() - off
        out["poc_dist"] = np.abs(pocs - price).min() if len(pocs) else np.nan
    if k >= bot["poc_lookback_sessions"]:
        prior = sessions.loc[usable[k - bot["poc_lookback_sessions"]:k], "poc_adj"].to_numpy()
        cl = np.array([g.price for g in cluster_pocs(prior, 8) if g.count >= bot["pocs_for_level"]]) - off
        out["cluster_dist"] = np.abs(cl - price).min() if len(cl) else np.nan
    kind = "supply" if d == -1 else "demand"
    z = zones[(zones["kind"] == kind) & (zones["created"] <= when) & (zones["dies"] > when)]
    if len(z):
        lo, hi = z["low"].to_numpy() - off, z["high"].to_numpy() + 0 - off
        dist = np.where(price < lo, lo - price, np.where(price > hi, price - hi, 0))
        out["zone_dist"] = dist.min()
    return out


def main() -> None:
    s = load_settings()
    tol = ticks(NEAR_POINTS, s)
    sessions, zones, offsets = es_context(s)
    t = pd.read_csv(OUT / "journal_trades.csv")
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c], utc=True)
    t = t[t["closed"]].copy()
    t["day"] = t["entry_time"].dt.tz_convert("America/New_York").dt.date.astype(str)
    t["mes"] = "MES" + t["contract"].str.extract(r"([HMUZ])\d(\d)$").agg("".join, axis=1)
    t["es"] = "ES" + t["contract"].str.extract(r"([HMUZ])\d(\d)$").agg("".join, axis=1)
    t["session"] = session_dates(t["entry_time"]).to_numpy()
    rng = np.random.default_rng(s["level_study"]["random_seed"])

    trade_rows, random_rows = [], []
    for (day, mes), g in t.groupby(["day", "mes"]):
        f = MES_DIR / f"{mes}_{day}.dbn.zst"
        if not f.exists():
            continue
        m = load_day(f, s)
        d = np.where(g["side"] == "long", 1, -1)
        t_in = epoch_seconds(g["entry_time"])
        t_out = epoch_seconds(g["exit_time"])
        e = np.rint(g["entry"].to_numpy() / 0.25).astype(np.int64)
        fe = features(t_in, e, d, m, tol)
        # how far each trade went for and against the owner while open
        mfe, mae = [], []
        for a, b_, ee, dd in zip(t_in, t_out, e, d):
            i, j = np.searchsorted(m["t"], a, "left"), np.searchsorted(m["t"], b_, "right")
            seg = m["p"][i:j] if j > i else m["p"][i:i + 1]
            mfe.append(((seg - ee) * dd).max() if len(seg) else 0)
            mae.append(((ee - seg) * dd).max() if len(seg) else 0)
        fe["mfe_points"] = np.array(mfe) * 0.25
        fe["mae_points"] = np.array(mae) * 0.25
        lv = [level_distances(sessions, zones, offsets, pd.Timestamp(sess), es, ee, dd, a, s)
              for sess, es, ee, dd, a in zip(g["session"], g["es"], e, d, t_in)]
        fe = pd.concat([fe, pd.DataFrame(lv)], axis=1)
        trade_rows.append(pd.concat([g.reset_index(drop=True), fe], axis=1))

        # random moments in the same window, both directions, price = last trade then
        lo, hi = m["tt"][0] + LOOKBACK_S, m["tt"][-1]
        if hi > lo:
            rt = np.sort(rng.uniform(lo, hi, size=20))
            rp = m["px"][np.searchsorted(m["tt"], rt, "right") - 1]
            rd = rng.choice([-1, 1], size=len(rt))
            fr = features(rt, rp, rd, m, tol)
            sess = session_dates(pd.Series(pd.to_datetime(rt, unit="s", utc=True)))
            lv = [level_distances(sessions, zones, offsets, ss, g["es"].iat[0], pp, dd, a, s)
                  for ss, pp, dd, a in zip(sess, rp, rd, rt)]
            random_rows.append(pd.concat([fr, pd.DataFrame(lv)], axis=1))

    trades = pd.concat(trade_rows, ignore_index=True)
    rand = pd.concat(random_rows, ignore_index=True)
    trades.to_csv(OUT / "replay_trades.csv", index=False)

    def shares(df):
        return {
            "absorption 100+ fitting the trade, within 2 pts, last 5 min": (df["match_100"] > 0).mean(),
            "absorption 50+ fitting the trade, within 2 pts, last 5 min": (df["match_50"] > 0).mean(),
            "opposite absorption 100+ (against the trade), last 5 min": (df["opp_100"] > 0).mean(),
            "more fitting than opposite burst contracts": (df["match_contracts"] > df["opp_contracts"]).mean(),
            "delta last 1 min against the trade (aggressors on the other side)": (df["delta_1m"] < 0).mean(),
            "price came 3+ pts toward the entry in the last 5 min": (df["move_5m"] >= 12).mean(),
            "price came 6+ pts toward the entry in the last 15 min": (df["move_15m"] >= 24).mean(),
            "within 2 pts of a recent uncovered POC": (df["poc_dist"] <= tol).mean(),
            "within 2 pts of a POC cluster": (df["cluster_dist"] <= tol).mean(),
            "inside or within 2 pts of a matching 15-min zone": (df["zone_dist"] <= tol).mean(),
        }
    win = trades["points"] > 0
    table = pd.DataFrame({"your entries": shares(trades), "random moments, same windows": shares(rand),
                          "your winners": shares(trades[win]), "your losers": shares(trades[~win])})
    table.to_csv(OUT / "replay_summary.csv")
    pd.set_option("display.width", 250)
    print(f"{len(trades)} trades replayed, {len(rand)} random moments")
    print((table * 100).round(0).to_string())
    print("\nwhile open: furthest in your favour (points)", trades["mfe_points"].describe(percentiles=[.25, .5, .75]).round(2).to_dict())
    print("while open: furthest against you (points)", trades["mae_points"].describe(percentiles=[.25, .5, .75]).round(2).to_dict())


if __name__ == "__main__":
    main()
