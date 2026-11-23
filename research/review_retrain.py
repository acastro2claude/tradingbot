"""Retrain the learned read with the owner's round-1 answers and checklist inputs, then test it on unseen days.

Owner (Nov 2026), after reviewing 53 of the bot's trades (b101-b153): the agreed next step is "I turn your
recurring reasons into inputs and retrain the bot with your answers added ... test it on the days kept out of
this round, against random entries ... If not, I'll tell you straight."

Training examples:
  - the replay clips as before (research.learn_read.clip_rows)
  - the round's reviews: "take" = enter now from 4 s before to 2 s after the bot's entry (earlier points more
    than 10 s before it = not yet), as for the owner's own presses; "pass" = not at any point up to the bot's
    entry (the clip stopped there); "unsure" left out
Inputs: the old ones, plus the owner's checklist (learn_read.swing_features, columns sw_*).
Four bots, so the two changes can be told apart:
  A. old inputs, replay clips only      (the round-1 bot; reproduces its trades)
  B. checklist inputs, replay clips only
  C. checklist inputs + the round's answers
  D. old inputs + the round's answers
The entry threshold for each comes from held-out days (5 folds by day), set so the bot takes as many replay
clips as the owner did.
Test: every 2026 MES day the owner never marked and that was not in the review (filters as always, one trade at a
time, -$500 day stop, stop-market 4.5, target 10, plain and with the stop to +1 at +7), against random entries
(20 a day, random side) and the old first-turn entry on the same days.
Writes research/results/journal/review_retrain_trades.csv, review_retrain_summary.csv, review_checklist.csv.
Run:   python -m research.review_retrain --round 1
"""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd

import research.learn_read as lr
from research.absorption_study import session_files
from research.bot_week_study import FLAT_ET, SLIP_TICKS, et
from research.entry_read import filters
from research.exits import bot_exit
from research.journal_study import OUT
from research.level_study import DERIVED
from research.level_test_book import TestReader, find_tests
from research.market_data import epoch_seconds, load_trades
from research.owner_rules import DayTrades
from research.replay_score import DECISIONS
from research.settings import load_settings

S = load_settings()
L = S["learn_read"]
CACHE = DERIVED / "review_retrain"
FILES = None


def day_trades(day: str) -> pd.DataFrame:
    global FILES
    if FILES is None:
        FILES = {d.date().isoformat(): (c, f) for d, c, f in session_files()}
    tr = load_trades(FILES[day][1], S)
    return pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                             size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))


def answers(rnd: int) -> pd.DataFrame:
    """The owner's answers on a review round, with the bot's trade (bot_review_round<N>.csv)."""
    rv = pd.read_csv(OUT / f"bot_review_round{rnd}.csv")
    ids = pd.read_csv(OUT / "replay_clip_ids.csv")
    dec = pd.DataFrame([json.load(open(f)) for f in sorted(DECISIONS.glob(f"b{rnd}*.json"))])
    dec = dec.merge(ids, left_on="clip_id", right_on="public_id")
    return rv.merge(dec[["clip_id", "id", "decision", "note"]], on="id", how="left")


def review_rows(rnd: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Training rows from a review round (labelled) and every point up to the bot's entry (for the record)."""
    a = answers(rnd)
    lab, full = [], []
    for day, g in a.groupby("day"):
        tr = day_trades(day)
        dt = DayTrades(trades=tr)
        b, nps = lr.day_bursts(tr, "MES"), lr.normal_per_second(dt, day)
        for r in g.itertuples():
            d = int(r.direction)
            far = int(r.lo) if d == 1 else int(r.hi)
            x = lr.features(dt, b, nps, r.test_time, d, int(r.edge), far, lr.TF_MIN.get(r.tf, 30),
                            float(r.zone_test) if r.zone_test == r.zone_test else 1.0, (r.hi - r.lo) * lr.TICK)
            t_in = r.entry_time - r.test_time
            x = x[x["t_since_test"] <= t_in + L["positive_after_seconds"]].copy()
            x["clip"], x["day"], x["market"], x["contract"], x["direction"] = r.id, day, "MES", r.contract, d
            x["owner_entry_time"], x["owner_entry_price"] = np.nan, np.nan
            x["review"], x["decision"] = r.clip_id, r.decision
            full.append(x.copy())
            if r.decision == "take":
                pos = x["t_since_test"] >= t_in - L["positive_before_seconds"]
                use = pos | (x["t_since_test"] < t_in - L["wait_gap_seconds"])
                x["label"] = pos.astype(int)
                x["owner_takes"] = True
                lab.append(x[use])
            elif r.decision == "pass":
                x = x[x["t_since_test"] <= t_in]
                x["label"], x["owner_takes"] = 0, False
                lab.append(x)
        print("review", day, len(g), flush=True)
    return pd.concat(lab, ignore_index=True), pd.concat(full, ignore_index=True)


def cached(name: str, make):
    p = CACHE / f"{name}.parquet"
    if p.exists():
        return pd.read_parquet(p)
    CACHE.mkdir(parents=True, exist_ok=True)
    x = make()
    x.to_parquet(p, index=False)
    return x


def train(data: pd.DataFrame, full: pd.DataFrame, feats: list, replay: set):
    """The model on all rows, and its threshold: held-out days, the owner's take rate on the replay clips."""
    from sklearn.model_selection import GroupKFold
    lr.FEATS = feats
    clips = data.groupby("clip").agg(day=("day", "first"), owner=("owner_takes", "first")).reset_index()
    held = []
    for tr_i, te_i in GroupKFold(n_splits=L["folds"]).split(clips, groups=clips["day"]):
        m = lr.fit(data[data["clip"].isin(set(clips.loc[tr_i, "clip"]))])
        te = set(clips.loc[te_i, "clip"]) & replay
        fp = full[full["clip"].isin(te)]
        held.append(pd.Series(m.predict_proba(fp[feats])[:, 1]).groupby(fp["clip"].to_numpy()).max())
    rate = clips.loc[clips["clip"].isin(replay), "owner"].mean()
    thr = float(pd.concat(held).quantile(1 - rate))
    return lr.fit(data), thr


def unseen_days(skip: set) -> list:
    from research.level_study import prepare
    _, sessions, _ = prepare(S)
    marked = set(pd.read_csv(OUT / "replay_tests_2026.csv")["day"])
    out = []
    for day, contract, f in session_files():
        d_str = day.date().isoformat()
        if d_str in marked or d_str in skip or day not in sessions.index or not sessions.loc[day, "use"] \
                or sessions.loc[day, "contract"] != contract:
            continue
        out.append((d_str, contract, int(sessions.loc[day, "offset_ticks"])))
    return out


def run_days(days: list, bots: dict) -> pd.DataFrame:
    """Every bot (and the baselines) on each unseen day; one row per trade."""
    from research.level_study import prepare
    from research.zone_book_study import zone_levels, zone_of
    from research.zones import all_zones
    fb, _, _ = prepare(S)
    zones, _ = all_zones(fb, S)
    loc = fb["ts"].dt.tz_convert("America/New_York")
    vol = fb.groupby([loc.dt.date.astype(str), loc.dt.strftime("%H:%M")])["volume"].sum()
    rng = np.random.default_rng(S["level_study"]["random_seed"])
    rows = []
    for d_str, contract, off in days:
        tr = day_trades(d_str)
        dt = DayTrades(trades=tr)
        b, nps = lr.day_bursts(tr, "MES"), lr.normal_per_second(dt, d_str)
        mv = vol.loc[d_str] if d_str in vol.index.get_level_values(0) else pd.Series(dtype=float)
        start, end, flat = et(d_str, "09:30"), et(d_str, "15:30"), et(d_str, FLAT_ET)
        tests = find_tests(SimpleNamespace(trades=tr), zone_levels(zones, off, start, end), start, end)
        entries = []
        for x in (tests.itertuples() if not tests.empty else []):
            if filters(d_str, x.time, mv, 0.0, 0.0) is not None:
                continue
            z = zone_of(x, zones, off)
            d = int(x.direction)
            far = int(x.lo) if d == 1 else int(x.hi)
            ft = lr.features(dt, b, nps, x.time, d, int(x.edge), far, lr.TF_MIN.get(z["tf"], 30),
                             float(z["zone_test"]) if z["zone_test"] == z["zone_test"] else 1.0, (x.hi - x.lo) * lr.TICK)
            for name, (m, thr, feats) in bots.items():
                k = lr.first_entry(m.predict_proba(ft[feats])[:, 1], thr)
                if k is not None:
                    entries.append(dict(who=name, t=float(ft["time"].iat[k]), px=int(ft["price"].iat[k]), d=d,
                                        test_time=x.time, edge=int(x.edge), tf=z["tf"],
                                        **{c: float(ft[c].iat[k]) for c in ft.columns if c.startswith("sw_")}))
            kk, _ = TestReader.turn_trade(dt, x.time, d, int(x.edge))
            if kk is not None:
                entries.append(dict(who="first turn", t=float(dt.t[kk]), px=int(dt.px[kk]), d=d, test_time=x.time,
                                    edge=int(x.edge), tf=z["tf"]))
        for t0 in rng.uniform(et(d_str, "10:00"), end, 20):
            i = int(np.searchsorted(dt.t, t0, "right"))
            if i < len(dt.t):
                entries.append(dict(who="random entries", t=float(dt.t[i]), px=int(dt.px[i]), d=int(rng.choice([-1, 1]))))
        for who in sorted({e["who"] for e in entries}):
            busy, pnl = -np.inf, 0.0
            for e in sorted((e for e in entries if e["who"] == who), key=lambda e: e["t"]):
                if who != "random entries" and (e["t"] < busy or pnl <= -S["bot"]["daily_loss_limit_usd"]):
                    continue
                fill = e["px"] + e["d"] * SLIP_TICKS
                pts, t_out, _ = bot_exit(dt, e["t"], fill, e["d"], flat, move=False)
                mpts, _, _ = bot_exit(dt, e["t"], fill, e["d"], flat, move=True)
                rows.append(dict(e, day=d_str, points_plain=pts * lr.TICK, points_move=mpts * lr.TICK))
                if who != "random entries":
                    busy, pnl = t_out, pnl + lr.usd(pts * lr.TICK)
        print(d_str, len(tests), flush=True)
    return pd.DataFrame(rows)


def summary(trades: pd.DataFrame) -> pd.DataFrame:
    out = []
    for who, g in trades.groupby("who"):
        for ex in ("plain", "move"):
            p = g[f"points_{ex}"]
            usd = lr.usd(p)
            mth = usd.groupby(g["day"].str[:7]).sum()
            out.append(dict(who=who, exit=ex, trades=len(g), days=g["day"].nunique(), win_rate=(p > 0).mean(),
                             usd_per_trade=usd.mean(), net_usd=usd.sum() if who != "random entries" else np.nan,
                             months_up=f"{int((mth > 0).sum())} of {len(mth)}" if who != "random entries" else ""))
    return pd.DataFrame(out)


def checklist_table(a: pd.DataFrame, rv: pd.DataFrame) -> pd.DataFrame:
    """The checklist inputs at the bot's entry on the reviewed trades, next to the owner's answer and the result."""
    cols = [c for c in rv.columns if c.startswith("sw_")]
    at = rv.merge(a[["clip_id", "entry_time"]], left_on="review", right_on="clip_id")
    at = at[(at["time"] - at["entry_time"]).abs() < 0.5]
    return a[["clip_id", "day", "direction", "decision", "points_plain", "note"]].merge(
        at[["review"] + cols], left_on="clip_id", right_on="review", how="left").drop(columns="review")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    rnd = ap.parse_args().round
    data = cached("clip_rows", lr.clip_rows)
    full = cached("clip_points", lr.all_points)
    if not (CACHE / f"review{rnd}_rows.parquet").exists():
        lab_, full_ = review_rows(rnd)
        cached(f"review{rnd}_rows", lambda: lab_)
        cached(f"review{rnd}_points", lambda: full_)
    rv_lab, rv_full = cached(f"review{rnd}_rows", None), cached(f"review{rnd}_points", None)
    a = answers(rnd)
    chk = checklist_table(a, rv_full)
    chk.to_csv(OUT / f"review{rnd}_checklist.csv", index=False)
    replay = set(data["clip"])
    new = lr.feature_names(data.drop(columns=["review", "decision"], errors="ignore"))
    old = [c for c in new if not c.startswith("sw_")]
    both = pd.concat([data, rv_lab.drop(columns=["review", "decision"])], ignore_index=True)
    both_full = pd.concat([full, rv_full[full.columns.intersection(rv_full.columns)]], ignore_index=True)
    bots = {}
    for name, d_, f_, feats in (("A. old inputs", data, full, old),
                                ("B. checklist inputs", data, full, new),
                                ("C. checklist inputs + answers", both, both_full, new),
                                ("D. old inputs + answers", both, both_full, old)):
        m, thr = train(d_, f_, feats, replay)
        bots[name] = (m, thr, feats)
        print(name, "threshold", round(thr, 3), flush=True)
    skip = set(pd.read_csv(OUT / f"bot_review_round{rnd}.csv")["day"])
    days = unseen_days(skip)
    print(len(days), "unseen days", flush=True)
    trades = run_days(days, bots)
    trades.to_csv(OUT / "review_retrain_trades.csv", index=False)
    t = summary(trades)
    t.to_csv(OUT / "review_retrain_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(t.round(2).to_string())


if __name__ == "__main__":
    main()
