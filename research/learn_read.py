"""Learn the owner's read from the marked replay clips, then trade it on days the owner never marked.

Owner (Nov 2026): the bot should run on its own. Hand-set rules for the read did no better than
random entries on unseen days (REPORT.md sections 22-25), while the owner's own picks made money.
So here the bot learns from the owner's decisions instead. Settings: [learn_read] (proposals).

Examples: the owner's careful decisions on the replay clips that pass the owner's filters (10:00,
news spikes, Fed days): the 2024 clips (the 45 quick ones left out) and the 2026 clips (the 3
marked with hindsight left out; "not sure" left out). One decision point every 2 s from the zone
test to 5 minutes after. Label "enter now" from 4 s before to 2 s after the owner pressed Long or
Short; "not yet / no" for every point of a skipped clip and the points of a taken clip more than
10 s before the press (points after the press are not used).

Features at each point use only what was known then (the tape; no order book, so the same bot can
run on the 2026 days that have no book): time since the test; where price is against the zone's
edges; the low since the test, the bounce off it, how long ago it was set, how many pushes; the
aggressive volume our way and against over 2 s to 2 minutes (against the day's normal), and their
balance; CVD since the test and since the low; how fast the market moves (range and volume over 1
and 5 minutes); the move into the zone over 10 and 30 minutes; absorption markers at the zone our
way and against; the zone's timeframe, test number and height; the time of day.

Model: small gradient-boosted trees (scikit-learn). The bot enters at the first point where the
model's "enter now" probability reaches a threshold; the threshold is set so the bot takes about
as many clips as the owner did.
  1. Cross-validation by day (5 folds): on held-out days, agreement with the owner and the money
     of the bot's entries, with the owner's exits.
  2. The real test: the model trained on all the examples trades every zone test (after the
     filters) on the 110 MES days of 2026 that the owner never marked: one trade at a time, -$500
     day stop, stop-market 4.5, target 10 (with and without the stop to +1 at +7). Baselines: random
     entries and the old first-turn entry on the same days.
Writes research/results/journal/learn_read_cv.csv, learn_read_2026.csv and learn_read_summary.csv.
Needs scikit-learn. Run:   python -m research.learn_read
"""

from __future__ import annotations

import glob
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd

from research.absorption import find_bursts
from research.absorption_study import session_files
from research.bot_week_study import FLAT_ET, SLIP_TICKS, et
from research.entry_read import day_trades_df, filters
from research.exits import bot_exit
from research.journal_study import OUT
from research.level_test_book import TestReader, find_tests
from research.market_data import epoch_seconds, load_trades
from research.owner_rules import DayTrades
from research.replay_score import DECISIONS, sample
from research.replay_score_2026 import HINDSIGHT
from research.settings import load_settings
from research.sustain_grid import normal_per_second

S = load_settings()
L = S["learn_read"]
TICK = S["market"]["tick_size"]
TF_MIN = {"15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240}


def usd(points):
    c = S["bot"]["contracts_per_trade"]
    return points * S["market"]["point_value_usd"] * c - S["backtest"]["commission_per_contract_round_trip"] * c


def day_bursts(tr: pd.DataFrame, market: str) -> pd.DataFrame:
    mn = L["burst_min_es"] if market == "ES" else L["burst_min_mes"]
    b = find_bursts(tr["t"].to_numpy(), tr["price"].to_numpy().astype(np.int64), tr["size"].to_numpy(),
                    tr["side"].to_numpy(), 0.01, mn)
    b["direction"] = np.where(b["side"] == "B", -1, 1)
    return b


def features(dt: DayTrades, bursts: pd.DataFrame, nps: float, T: float, d: int, near: int, far: int,
             tf_min: float, test_no: float, height_pts: float) -> pd.DataFrame:
    """One row per decision point (every step_seconds from the test to 5 minutes after), known-at-the-time only."""
    step, n = L["step_seconds"], 300 // L["step_seconds"] + 1
    pts = T + np.arange(n) * step
    y = dt.px * d
    ours = np.concatenate([[0], np.cumsum(dt.buy if d == 1 else dt.sell)])
    push = np.concatenate([[0], np.cumsum(dt.sell if d == 1 else dt.buy)])
    idx = np.searchsorted(dt.t, pts, "right")                     # trades up to each point
    last = y[np.maximum(idx - 1, 0)] / 4.0                          # points, our-way view
    nearY, farY = near * d / 4.0, far * d / 4.0
    i0 = int(np.searchsorted(dt.t, T, "left"))
    # the low since the test (our-way view), when it was set, pushes (new lows after a 1-point bounce)
    low = np.empty(n)
    low_t = np.empty(n)
    pushes = np.zeros(n)
    cur_low, cur_t, n_push, bounced = np.inf, T, 0, True
    prev = i0
    for k in range(n):
        seg = y[prev:idx[k]] / 4.0
        tt = dt.t[prev:idx[k]]
        for v, t_ in zip(seg, tt):
            if v < cur_low:
                if bounced:
                    n_push += 1
                    bounced = False
                cur_low, cur_t = v, t_
            elif v - cur_low >= 1.0:
                bounced = True
        prev = idx[k]
        low[k], low_t[k], pushes[k] = (cur_low if np.isfinite(cur_low) else last[k]), cur_t, n_push
    f = dict(t_since_test=pts - T, dist_near=last - nearY, depth=nearY - low, through_far=farY - low,
             bounce=last - low, secs_since_low=pts - low_t, pushes=pushes)
    for w in L["windows_seconds"]:
        j = np.searchsorted(dt.t, pts - w, "right")
        o, p = ours[idx] - ours[j], push[idx] - push[j]
        f[f"ours_{w}s"] = o / (nps * w)
        f[f"against_{w}s"] = p / (nps * w)
        f[f"balance_{w}s"] = (o - p) / np.maximum(o + p, 1)
    cvd = (ours[idx] - push[idx]) - (ours[i0] - push[i0])
    k_low = np.searchsorted(dt.t, low_t, "right")
    f["cvd_since_test"] = cvd / (nps * 60)
    f["cvd_since_low"] = ((ours[idx] - push[idx]) - (ours[k_low] - push[k_low])) / (nps * 60)
    for w in (60, 300):
        j = np.searchsorted(dt.t, pts - w, "right")
        f[f"range_{w}s"] = np.array([(y[a:b].max() - y[a:b].min()) / 4.0 if b > a else 0.0 for a, b in zip(j, idx)])
        f[f"volume_{w}s"] = ((ours[idx] - ours[j]) + (push[idx] - push[j])) / (2 * nps * w)
    for m in (10, 30):
        j = max(int(np.searchsorted(dt.t, T - m * 60, "right")) - 1, 0)
        f[f"move_in_{m}m"] = (y[j] - y[max(i0 - 1, 0)]) / 4.0
    band_lo, band_hi = min(near, far) - 4, max(near, far) + 4
    bz = bursts[(bursts["price"] >= band_lo) & (bursts["price"] <= band_hi)]
    bt_ours, bt_against = bz.loc[bz["direction"] == d, "time"].to_numpy(), bz.loc[bz["direction"] == -d, "time"].to_numpy()
    for w in (60, 300):
        f[f"markers_ours_{w}s"] = np.searchsorted(bt_ours, pts) - np.searchsorted(bt_ours, pts - w)
        f[f"markers_against_{w}s"] = np.searchsorted(bt_against, pts) - np.searchsorted(bt_against, pts - w)
    f["zone_minutes"] = np.full(n, tf_min)
    f["zone_test"] = np.full(n, min(test_no, 6))
    f["zone_height"] = np.full(n, height_pts)
    loc = pd.Timestamp(T, unit="s", tz="UTC").tz_convert("America/New_York")
    f["minutes_after_open"] = np.full(n, loc.hour * 60 + loc.minute - 570)
    out = pd.DataFrame(f)
    out["time"] = pts
    out["price"] = (y[np.maximum(idx - 1, 0)] * d).astype(np.int64)
    return out


def marked_clips() -> pd.DataFrame:
    dec = pd.DataFrame([json.load(open(f)) for f in sorted(glob.glob(str(DECISIONS / "*.json")))])
    ids = pd.read_csv(OUT / "replay_clip_ids.csv")
    t24, t26 = pd.read_csv(OUT / "replay_tests.csv"), pd.read_csv(OUT / "replay_tests_2026.csv")
    tests = pd.concat([t24, t26], ignore_index=True)
    d = dec.merge(ids, left_on="clip_id", right_on="public_id").merge(tests, on="id")
    d = d[d["decision"].isin(["long", "short", "skip"])]
    d = d.sort_values("round").drop_duplicates("id", keep="last")
    note = d["note"].fillna("").str.strip()
    quick = [sample(a, r) == "new clips, second sitting" and not n_ for a, r, n_ in zip(d["saved_at"], d["round"], note)]
    d = d[~np.array(quick)]
    d = d[~(d["decision"].isin(["long", "short"]) & note.str.lower().apply(lambda s_: any(w in s_ for w in HINDSIGHT)))]
    inv = pd.read_csv(OUT / "entry_read_replay.csv").set_index("clip")["filter"]
    keep = [(c not in inv.index) or pd.isna(inv.get(c)) for c in d["clip_id"]]   # 2026 clips passed the filters at export
    return d[np.array(keep)].reset_index(drop=True)


def clip_rows() -> pd.DataFrame:
    d = marked_clips()
    rows = []
    for (market, contract, day), g in d.groupby(["market", "contract", "day"]):
        tr = day_trades_df(market, contract, day)
        dt = DayTrades(trades=tr)
        b, nps = day_bursts(tr, market), normal_per_second(dt, day)
        for r in g.itertuples():
            far = int(r.lo) if r.direction == 1 else int(r.hi)
            x = features(dt, b, nps, r.time, int(r.direction), int(r.edge), far, TF_MIN.get(r.tf, 30),
                         float(r.zone_test), (r.hi - r.lo) * TICK)
            take = r.decision in ("long", "short")
            if take:
                dt_in = max(r.entry_time - r.time, 0.0)
                pos = (x["t_since_test"] >= dt_in - L["positive_before_seconds"]) & (x["t_since_test"] <= dt_in + L["positive_after_seconds"])
                use = pos | (x["t_since_test"] < dt_in - L["wait_gap_seconds"])
                x["label"] = pos.astype(int)
                x = x[use]
            else:
                x["label"] = 0
            x["clip"], x["day"], x["market"], x["contract"], x["direction"] = r.clip_id, day, market, contract, int(r.direction)
            x["owner_takes"], x["owner_entry_time"] = take, (r.entry_time if take else np.nan)
            x["owner_entry_price"] = r.entry_price if take else np.nan
            rows.append(x)
        print(day, market, len(g), flush=True)
    return pd.concat(rows, ignore_index=True)


FEATS = None


def model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_depth=L["model_max_depth"], learning_rate=L["model_learning_rate"],
                                          max_iter=L["model_max_iter"], min_samples_leaf=L["model_min_samples_leaf"],
                                          random_state=S["level_study"]["random_seed"])


def fit(x: pd.DataFrame):
    w = np.where(x["label"] == 1, (x["label"] == 0).sum() / max((x["label"] == 1).sum(), 1), 1.0)
    m = model()
    m.fit(x[FEATS], x["label"], sample_weight=w)
    return m


def first_entry(p: np.ndarray, thr: float) -> int | None:
    k = np.flatnonzero(p >= thr)
    return int(k[0]) if len(k) else None


def main() -> None:
    global FEATS
    from sklearn.model_selection import GroupKFold
    data = clip_rows()
    FEATS = [c for c in data.columns if c not in ("label", "time", "price", "clip", "day", "market", "contract",
                                                  "direction", "owner_takes", "owner_entry_time", "owner_entry_price")]
    clips = data.groupby("clip").agg(day=("day", "first"), owner=("owner_takes", "first")).reset_index()
    print(len(clips), "clips,", int(clips["owner"].sum()), "taken;", len(data), "decision points,", int(data["label"].sum()), "positive")
    # 1. cross-validation by day: every decision point of a held-out clip is scored (not only the labelled ones)
    full = all_points()
    gkf = GroupKFold(n_splits=L["folds"])
    cv_rows, held_max = [], []
    days = clips["day"].to_numpy()
    for k, (tr_i, te_i) in enumerate(gkf.split(clips, groups=days)):
        tr_clips, te_clips = set(clips.loc[tr_i, "clip"]), set(clips.loc[te_i, "clip"])
        m = fit(data[data["clip"].isin(tr_clips)])
        fp = full[full["clip"].isin(te_clips)].copy()
        fp["p"] = m.predict_proba(fp[FEATS])[:, 1]
        tp = full[full["clip"].isin(tr_clips)].copy()
        tp["p"] = m.predict_proba(tp[FEATS])[:, 1]
        take_rate = clips.loc[tr_i, "owner"].mean()
        thr = float(tp.groupby("clip")["p"].max().quantile(1 - take_rate))   # bot takes as many training clips as the owner
        held_max.append(fp.groupby("clip")["p"].max())
        for c, g in fp.groupby("clip"):
            k_ = first_entry(g["p"].to_numpy(), thr)
            cv_rows.append(dict(clip=c, fold=k, thr=thr, bot_takes=k_ is not None,
                                bot_time=float(g["time"].iat[k_]) if k_ is not None else np.nan,
                                bot_price=int(g["price"].iat[k_]) if k_ is not None else np.nan))
    cv = pd.DataFrame(cv_rows).merge(clip_info(), on="clip")
    cv = score_clips(cv)
    cv.to_csv(OUT / "learn_read_cv.csv", index=False)
    # 2. the final model on the unmarked 2026 days
    m = fit(data)
    # the threshold from held-out scores, so the bot takes about as many clips as the owner on days it didn't see
    thr = float(pd.concat(held_max).quantile(1 - clips["owner"].mean()))
    y26 = unmarked_2026(m, thr)
    y26.to_csv(OUT / "learn_read_2026.csv", index=False)
    summary(cv, y26, thr)


CLIP_INFO = {}
STATS = {}


def clip_info() -> pd.DataFrame:
    return pd.DataFrame(CLIP_INFO.values())


def all_points() -> pd.DataFrame:
    """Every decision point of every marked clip (the labelled set drops the points after an owner's press)."""
    d = marked_clips()
    rows = []
    for (market, contract, day), g in d.groupby(["market", "contract", "day"]):
        tr = day_trades_df(market, contract, day)
        dt = DayTrades(trades=tr)
        b, nps = day_bursts(tr, market), normal_per_second(dt, day)
        for r in g.itertuples():
            far = int(r.lo) if r.direction == 1 else int(r.hi)
            x = features(dt, b, nps, r.time, int(r.direction), int(r.edge), far, TF_MIN.get(r.tf, 30),
                         float(r.zone_test), (r.hi - r.lo) * TICK)
            x["clip"] = r.clip_id
            rows.append(x)
            take = r.decision in ("long", "short")
            CLIP_INFO[r.clip_id] = dict(clip=r.clip_id, day=day, market=market, contract=contract, direction=int(r.direction),
                                        test_time=r.time, edge=int(r.edge), owner_takes=take,
                                        owner_time=r.entry_time if take else np.nan,
                                        owner_price=round(r.entry_price / TICK) if take else np.nan,
                                        owner_dir=(1 if r.decision == "long" else -1) if take else 0)
    return pd.concat(rows, ignore_index=True)


def score_clips(cv: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (market, contract, day), g in cv.groupby(["market", "contract", "day"]):
        dt = DayTrades(trades=day_trades_df(market, contract, day))
        flat = et(day, FLAT_ET)
        for r in g.itertuples():
            row = r._asdict()
            for who, t_, p_, d_ in (("bot", r.bot_time, r.bot_price, r.direction),
                                    ("owner", r.owner_time, r.owner_price, r.owner_dir)):
                if pd.isna(t_) or d_ == 0:
                    continue
                for ex, mv in (("plain", False), ("move", True)):
                    row[f"{who}_{ex}"] = bot_exit(dt, float(t_), int(p_) + d_ * SLIP_TICKS, d_, flat, move=mv)[0] * TICK
            k, _ = TestReader.turn_trade(dt, r.test_time, r.direction, r.edge)
            if k is not None:
                for ex, mv in (("plain", False), ("move", True)):
                    row[f"first_turn_{ex}"] = bot_exit(dt, float(dt.t[k]), int(dt.px[k]) + r.direction * SLIP_TICKS,
                                                       r.direction, flat, move=mv)[0] * TICK
            out.append(row)
    return pd.DataFrame(out)


def unmarked_2026(m, thr: float) -> pd.DataFrame:
    from research.level_study import prepare
    from research.zone_book_study import zone_levels, zone_of
    from research.zones import all_zones
    fb, sessions, _ = prepare(S)
    zones, _ = all_zones(fb, S)
    loc = fb["ts"].dt.tz_convert("America/New_York")
    vol = fb.groupby([loc.dt.date.astype(str), loc.dt.strftime("%H:%M")])["volume"].sum()
    marked = set(pd.read_csv(OUT / "replay_tests_2026.csv")["day"])
    rng = np.random.default_rng(S["level_study"]["random_seed"])
    rows = []
    for day, contract, f in session_files():
        d_str = day.date().isoformat()
        if d_str in marked or day not in sessions.index or not sessions.loc[day, "use"] or sessions.loc[day, "contract"] != contract:
            continue
        off = int(sessions.loc[day, "offset_ticks"])
        tr = load_trades(f, S)
        tr = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                               size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        dt = DayTrades(trades=tr)
        b, nps = day_bursts(tr, "MES"), normal_per_second(dt, d_str)
        mv = vol.loc[d_str] if d_str in vol.index.get_level_values(0) else pd.Series(dtype=float)
        start, end, flat = et(d_str, "09:30"), et(d_str, "15:30"), et(d_str, FLAT_ET)
        tests = find_tests(SimpleNamespace(trades=tr), zone_levels(zones, off, start, end), start, end)
        entries = []
        for x in (tests.itertuples() if not tests.empty else []):
            STATS["tests"] = STATS.get("tests", 0) + 1
            if filters(d_str, x.time, mv, 0.0, 0.0) is not None:
                continue
            z = zone_of(x, zones, off)
            d = int(x.direction)
            far = int(x.lo) if d == 1 else int(x.hi)
            ft = features(dt, b, nps, x.time, d, int(x.edge), far, TF_MIN.get(z["tf"], 30),
                          float(z["zone_test"]) if z["zone_test"] == z["zone_test"] else 1.0, (x.hi - x.lo) * TICK)
            p = m.predict_proba(ft[FEATS])[:, 1]
            k = first_entry(p, thr)
            if k is not None:
                STATS["fired"] = STATS.get("fired", 0) + 1
                entries.append(("learned read", float(ft["time"].iat[k]), int(ft["price"].iat[k]), d))
            kk, _ = TestReader.turn_trade(dt, x.time, d, int(x.edge))
            if kk is not None:
                entries.append(("old bot: first turn", float(dt.t[kk]), int(dt.px[kk]), d))
        for t0 in rng.uniform(et(d_str, "10:00"), end, 20):
            i = int(np.searchsorted(dt.t, t0, "right"))
            if i < len(dt.t):
                entries.append(("random entries", float(dt.t[i]), int(dt.px[i]), int(rng.choice([-1, 1]))))
        for ex, mv_ in (("plain 4.5/10", False), ("stop to +1 at +7", True)):
            for who in ("learned read", "old bot: first turn", "random entries"):
                busy, pnl = -np.inf, 0.0
                for name, t_, p_, d in sorted(e for e in entries if e[0] == who):
                    if who != "random entries" and (t_ < busy or pnl <= -S["bot"]["daily_loss_limit_usd"]):
                        continue
                    pts, t_out, _ = bot_exit(dt, t_, p_ + d * SLIP_TICKS, d, flat, move=mv_)
                    rows.append(dict(who=who, exit=ex, day=d_str, points=pts * TICK, usd=usd(pts * TICK)))
                    if who != "random entries":
                        busy, pnl = t_out, pnl + usd(pts * TICK)
        print(d_str, flush=True)
    return pd.DataFrame(rows)


def summary(cv: pd.DataFrame, y26: pd.DataFrame, thr: float) -> None:
    out = []
    agree = (cv["bot_takes"] == cv["owner_takes"]).mean()
    for ex in ("plain", "move"):
        for name, col, m in (("owner, held-out clips", f"owner_{ex}", cv["owner_takes"]),
                             ("learned read, held-out clips", f"bot_{ex}", cv["bot_takes"]),
                             ("learned read, clips the owner took", f"bot_{ex}", cv["bot_takes"] & cv["owner_takes"]),
                             ("learned read, clips the owner skipped", f"bot_{ex}", cv["bot_takes"] & ~cv["owner_takes"]),
                             ("first turn, all held-out clips", f"first_turn_{ex}", cv[f"first_turn_{ex}"].notna())):
            v = cv.loc[m, col].dropna()
            out.append(dict(test="cross-validation (held-out days)", exit=ex, who=name, trades=len(v),
                            win_rate=(v > 0).mean(), usd_per_trade=usd(v).mean(), net_usd=usd(v).sum()))
    for (who, ex), g in y26.groupby(["who", "exit"]):
        mth = g.groupby(g["day"].str[:7])["usd"].sum()
        out.append(dict(test="2026 days never marked", exit=ex, who=who, trades=len(g), win_rate=(g["points"] > 0).mean(),
                        usd_per_trade=g["usd"].mean(), net_usd=g["usd"].sum() if who != "random entries" else np.nan,
                        months_up=f"{int((mth > 0).sum())} of {len(mth)}" if who != "random entries" else ""))
    t = pd.DataFrame(out)
    t.to_csv(OUT / "learn_read_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(f"held-out agreement with the owner: {agree:.0%}; bot took {cv['bot_takes'].mean():.0%} of clips, owner {cv['owner_takes'].mean():.0%}; final threshold {thr:.3f}")
    print(f"2026 unmarked: {STATS.get('tests', 0)} zone tests after the filters, the learned read fired on {STATS.get('fired', 0)}")
    print(t.round(2).to_string())


if __name__ == "__main__":
    main()
