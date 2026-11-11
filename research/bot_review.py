"""Review rounds: the owner says yes or no to the learned bot's trades, and the bot is retrained.

Owner (Nov 2026): "What if I review them and then decide on whether I would have also taken that
trade or not. And then based on all the data we have, you can keep retraining the bot." Clips
without the heatmap are fine (no order book for these days); rounds of about 50 trades.

A round (python -m research.bot_review --round 1):
  1. train the learned read (research.learn_read: tape only) on every decision so far: the replay
     clips and the reviews of earlier rounds (a review "I'd take it" = enter at the bot's moment,
     "I'd pass" = not there); the entry threshold comes from held-out days as before
  2. run it on the 2026 days the owner never marked (the owner's filters, one trade at a time, -$500
     day stop) and log every trade with its reasons (SHAP, written in trading terms)
  3. pick a random set of those days, spread over the months, whose trades add up to about 50
     (the other days stay unseen for testing); each trade becomes a clip that ends at the bot's
     entry, with the bot's reasons, and no result shown
Writes research/results/journal/bot_review_round<N>.csv and the clips data/derived/replay/tests/BOT<N>-*.json;
research.replay_page puts them first on the replay page as b<N>01 ...
"""

from __future__ import annotations

import argparse
import base64
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd

import research.learn_read as lr
from research.absorption import find_bursts
from research.absorption_study import session_files
from research.bot_week_study import FLAT_ET, SLIP_TICKS, et
from research.entry_read import filters
from research.exits import bot_exit
from research.journal_study import OUT
from research.level_study import DERIVED
from research.learn_explain import phrase
from research.level_test_book import find_tests
from research.market_data import epoch_seconds, load_trades
from research.orderbook import Book
from research.owner_rules import DayTrades
from research.replay_export import REPLAY_DIR, clip
from research.settings import load_settings

S = load_settings()
G = S["bot_review"]
TICK = S["market"]["tick_size"]


def train(extra: pd.DataFrame | None = None):
    """The learned read on every decision so far, and its held-out threshold."""
    from sklearn.model_selection import GroupKFold
    data = lr.clip_rows()
    full = lr.all_points()
    if extra is not None and len(extra):
        data = pd.concat([data, extra], ignore_index=True)
        full = pd.concat([full, extra.drop(columns=["label"])], ignore_index=True)
    lr.FEATS = lr.feature_names(data)
    clips = data.groupby("clip").agg(day=("day", "first"), owner=("owner_takes", "first")).reset_index()
    held = []
    for tr_i, te_i in GroupKFold(n_splits=lr.L["folds"]).split(clips, groups=clips["day"]):
        m = lr.fit(data[data["clip"].isin(set(clips.loc[tr_i, "clip"]))])
        fp = full[full["clip"].isin(set(clips.loc[te_i, "clip"]))]
        held.append(pd.Series(m.predict_proba(fp[lr.FEATS])[:, 1]).groupby(fp["clip"].to_numpy()).max())
    thr = float(pd.concat(held).quantile(1 - clips["owner"].mean()))
    return lr.fit(data), thr


def run_unmarked(m, thr: float, skip_days: set) -> pd.DataFrame:
    import shap
    from research.level_study import prepare
    from research.zone_book_study import zone_levels, zone_of
    from research.zones import all_zones
    fb, sessions, _ = prepare(S)
    zones, candles = all_zones(fb, S)
    loc = fb["ts"].dt.tz_convert("America/New_York")
    vol = fb.groupby([loc.dt.date.astype(str), loc.dt.strftime("%H:%M")])["volume"].sum()
    marked = set(pd.read_csv(OUT / "replay_tests_2026.csv")["day"])
    ex = shap.TreeExplainer(m)
    rows = []
    for day, contract, f in session_files():
        d_str = day.date().isoformat()
        if d_str in marked or d_str in skip_days or day not in sessions.index or not sessions.loc[day, "use"] \
                or sessions.loc[day, "contract"] != contract:
            continue
        off = int(sessions.loc[day, "offset_ticks"])
        tr = load_trades(f, S)
        tr = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                               size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        dt = DayTrades(trades=tr)
        b, nps = lr.day_bursts(tr, "MES"), lr.normal_per_second(dt, d_str)
        mv = vol.loc[d_str] if d_str in vol.index.get_level_values(0) else pd.Series(dtype=float)
        start, end, flat = et(d_str, "09:30"), et(d_str, "15:30"), et(d_str, FLAT_ET)
        tests = find_tests(SimpleNamespace(trades=tr), zone_levels(zones, off, start, end), start, end)
        cand = []
        for x in (tests.itertuples() if not tests.empty else []):
            if filters(d_str, x.time, mv, 0.0, 0.0) is not None:
                continue
            z = zone_of(x, zones, off)
            d = int(x.direction)
            far = int(x.lo) if d == 1 else int(x.hi)
            ft = lr.features(dt, b, nps, x.time, d, int(x.edge), far, lr.TF_MIN.get(z["tf"], 30),
                             float(z["zone_test"]) if z["zone_test"] == z["zone_test"] else 1.0, (x.hi - x.lo) * lr.TICK)
            p = m.predict_proba(ft[lr.FEATS])[:, 1]
            k = lr.first_entry(p, thr)
            if k is None:
                continue
            sv = np.array(ex.shap_values(ft[lr.FEATS].iloc[[k]])).reshape(-1)[-len(lr.FEATS):]
            order = np.argsort(-sv)
            row_f = ft.iloc[k]
            cand.append(dict(day=d_str, contract=contract, off=off, test_time=x.time, direction=d, edge=int(x.edge),
                             lo=int(x.lo), hi=int(x.hi), tf=z["tf"], pattern=z["pattern"], zone_test=z["zone_test"],
                             formed=z["formed"], entry_time=float(row_f["time"]), entry_price=int(row_f["price"]),
                             p=float(p[k]),
                             reasons_for=" | ".join(phrase(lr.FEATS[i], float(row_f[lr.FEATS[i]]), d) for i in order[:4]),
                             reasons_against=" | ".join(phrase(lr.FEATS[i], float(row_f[lr.FEATS[i]]), d) for i in order[::-1][:2]),
                             **{f"f_{c}": float(row_f[c]) for c in lr.FEATS}))
        busy, pnl = -np.inf, 0.0
        for c in sorted(cand, key=lambda r: r["entry_time"]):
            if c["entry_time"] < busy or pnl <= -S["bot"]["daily_loss_limit_usd"]:
                continue
            pts, t_out, _ = bot_exit(dt, c["entry_time"], c["entry_price"] + c["direction"] * SLIP_TICKS, c["direction"], flat,
                                     move=False)
            c["points_plain"] = pts * lr.TICK
            busy, pnl = t_out, pnl + lr.usd(pts * lr.TICK)
            rows.append(c)
        print(d_str, len(cand), flush=True)
    return pd.DataFrame(rows), candles


def pick_days(trades: pd.DataFrame, n_target: int, seed: int) -> set:
    rng = np.random.default_rng(seed)
    per_day = trades.groupby("day").size()
    months = {m: list(rng.permutation(g.index.to_numpy())) for m, g in per_day.groupby(per_day.index.str[:7])}
    chosen, total = set(), 0
    while total < n_target and any(months.values()):
        for mth in sorted(months):
            if months[mth] and total < n_target:
                d = months[mth].pop(0)
                chosen.add(d)
                total += int(per_day[d])
    return chosen


def make_clips(trades: pd.DataFrame, candles: dict, rnd: int) -> pd.DataFrame:
    R = S["replay"]
    ctx = candles[R["context_minutes"]]
    sess = sorted(ctx["session"].unique())
    files = {d.date().isoformat(): f for d, c, f in session_files()}
    out = []
    for day, g in trades.groupby("day"):
        tr = load_trades(files[day], S)
        tr = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                               size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        book = Book(t0=int(tr["t"].iat[0]) - 1000, lo=int(tr["price"].min()) - 200, bid=np.zeros((2, 800), np.int64),
                    ask=np.zeros((2, 800), np.int64), flows=pd.DataFrame(columns=["sec", "side", "price", "added", "pulled", "filled"]),
                    trades=tr)
        bu = find_bursts(tr["t"].to_numpy(), tr["price"].to_numpy(), tr["size"].to_numpy(), tr["side"].to_numpy(),
                         R["burst_seconds"], R["burst_min_mes_matched"])
        bu["direction"] = np.where(bu["colour"] == "purple", -1, 1)
        s0, s1 = et(day, "09:30"), et(day, "15:30")
        rth = tr[(tr["t"] >= s0) & (tr["t"] < s1)]
        per = rth.assign(f=(rth["t"] // R["frame_seconds"]).astype(int)).groupby(["f", "price", "side"])["size"].sum()
        bubble_min = float(per.quantile(1 - R["bubble_top_share"])) if len(per) else 1.0
        k = sess.index(pd.Timestamp(day)) if pd.Timestamp(day) in sess else None
        keep = sess[max(0, k - R["context_sessions"]):k + 1] if k is not None else []
        ctx_c = ctx[ctx["session"].isin(keep)]
        for n, r in enumerate(g.sort_values("entry_time").itertuples()):
            cid = f"BOT{rnd}-{day}-{n:02d}"
            x = SimpleNamespace(time=r.test_time, direction=r.direction, edge=r.edge, lo=r.lo, hi=r.hi)
            loc = pd.Timestamp(r.test_time, unit="s", tz="UTC").tz_convert("America/New_York")
            meta = dict(id=cid, market="MES", contract=r.contract, weekday=loc.strftime("%A"), time_et=loc.strftime("%H:%M:%S"),
                        tf=r.tf, pattern=r.pattern,
                        zone_test=r.zone_test, formed=r.formed)
            c = clip(book, 1.0, bu, x, meta, ctx_c, r.off, R, bubble_min)
            fs = c["frame_seconds"]
            last = int(round((r.entry_time - c["start_epoch"]) / fs)) - 1          # the frame the bot enters at
            raw = base64.b64decode(c["heat"])
            c["heat"] = base64.b64encode(raw[:(last + 1) * c["rows"]]).decode()
            for key in ("close", "high", "low", "buy", "sell", "vwap"):
                c[key] = c[key][:last + 1]
            c["frames"] = last + 1
            c["bubbles"] = [b_ for b_ in c["bubbles"] if b_[0] <= last]
            c["bursts"] = [b_ for b_ in c["bursts"] if b_[0] <= last]
            c["burst_min"] = int(R["burst_min_mes_matched"])
            c["bot"] = dict(side="long" if r.direction == 1 else "short", frame=last, price_ticks=int(r.entry_price),
                            secs_after_test=round(r.entry_time - r.test_time), reasons_for=r.reasons_for.split(" | "),
                            reasons_against=r.reasons_against.split(" | "))
            c["no_book"] = True
            (REPLAY_DIR / f"{cid}.json").write_text(json.dumps(c, separators=(",", ":")))
            out.append(dict(id=cid, day=day, entry_time=r.entry_time, entry_price=r.entry_price, direction=r.direction))
        print("clips", day, len(g), flush=True)
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    rnd = ap.parse_args().round
    done = set()
    for k in range(1, rnd):
        prev = pd.read_csv(OUT / f"bot_review_round{k}.csv")
        done |= set(prev["day"])
    cache = DERIVED / f"bot_review_round{rnd}_trades.parquet"
    if cache.exists():                                   # the bot's trades for this round (training and running take a while)
        from research.level_study import prepare
        from research.zones import all_zones
        trades = pd.read_parquet(cache)
        _, candles = all_zones(prepare(S)[0], S)
    else:
        m, thr = train(None if rnd == 1 else review_examples(rnd))
        trades, candles = run_unmarked(m, thr, done)
        trades["threshold"] = thr
        trades.to_parquet(cache, index=False)
    thr = float(trades["threshold"].iat[0])
    pick = pick_days(trades, G["round_trades"], S["level_study"]["random_seed"] + G["seed_offset"] + rnd)
    rev = trades[trades["day"].isin(pick)].copy()
    ids = make_clips(rev, candles, rnd)
    rev = rev.sort_values(["day", "entry_time"]).reset_index(drop=True)
    rev["id"] = ids.sort_values(["day", "entry_time"])["id"].to_numpy()
    rev["threshold"] = thr
    rev.to_csv(OUT / f"bot_review_round{rnd}.csv", index=False)
    trades.assign(in_review=trades["day"].isin(pick)).to_csv(OUT / f"bot_review_round{rnd}_all_trades.csv", index=False)
    print(f"round {rnd}: {len(trades)} bot trades on {trades['day'].nunique()} unseen days; {len(rev)} on {len(pick)} days to review")


def review_examples(rnd: int) -> pd.DataFrame:
    raise NotImplementedError("rounds after the first add the owner's reviews to the training examples")





# ---------- the bot's trade in the owner's terms ----------

def pushes_into_zone(dt: DayTrades, T: float, t_entry: float, d: int, near: int) -> list:
    """Pushes into the zone, in the owner's sense: price trades at or into the zone; a retest counts only after
    price has moved at least 2 points back out of the zone and then returned. For each push: its extreme (the low
    for a long, the high for a short), our-way CVD there, and the aggressive volume of the leg into it."""
    i0, i1 = int(np.searchsorted(dt.t, T - 60)), int(np.searchsorted(dt.t, t_entry, "right"))
    y, nearY = dt.px * d, near * d
    against = dt.sell if d == 1 else dt.buy
    ours = dt.buy if d == 1 else dt.sell
    cvd = np.cumsum(ours - against)
    away = 8                                                  # 2 points back out of the zone
    out, state, ext_k, leg0, top_k = [], "in", None, i0, None
    for k in range(int(np.searchsorted(dt.t, T)), i1):
        if state == "in":
            if ext_k is None or y[k] < y[ext_k]:
                ext_k = k
            if y[k] >= nearY + away:                          # moved back out of the zone: the push is over
                out.append(dict(t=dt.t[ext_k], y=y[ext_k], cvd=cvd[ext_k], push_vol=int(against[leg0:ext_k + 1].sum())))
                state, top_k = "away", k
        else:
            if y[k] > y[top_k]:
                top_k = k
            if y[k] <= nearY + 1:                             # back at the zone: a retest
                state, ext_k, leg0 = "in", k, top_k
    if state == "in" and ext_k is not None:
        out.append(dict(t=dt.t[ext_k], y=y[ext_k], cvd=cvd[ext_k], push_vol=int(against[leg0:ext_k + 1].sum())))
    return out


def owner_view(dt: DayTrades, bursts: pd.DataFrame, nps: float, r) -> dict:
    """A summary and the owner's checklist for one bot trade (what was known at the entry)."""
    d, T, te = int(r.direction), float(r.test_time), float(r.entry_time)
    us, them = ("buyers", "sellers") if d == 1 else ("sellers", "buyers")
    ext_word = "low" if d == 1 else "high"
    i_e = int(np.searchsorted(dt.t, te, "right"))
    ours = np.concatenate([[0], np.cumsum(dt.buy if d == 1 else dt.sell)])
    agst = np.concatenate([[0], np.cumsum(dt.sell if d == 1 else dt.buy)])

    def share(w):
        j = int(np.searchsorted(dt.t, te - w, "right"))
        o, a = ours[i_e] - ours[j], agst[i_e] - agst[j]
        return (o / (o + a) if o + a else 0.5), o + a
    s5, _ = share(5)
    s15, _ = share(15)
    s120, _ = share(120)
    j60 = int(np.searchsorted(dt.t, te - 60, "right"))
    seg = dt.px[j60:i_e]
    rng60 = (seg.max() - seg.min()) * TICK if len(seg) else 0.0
    vol60 = ((ours[i_e] - ours[j60]) + (agst[i_e] - agst[j60])) / (2 * nps * 60) if nps else 0.0
    near, far = int(r.edge), (int(r.lo) if d == 1 else int(r.hi))
    ps = pushes_into_zone(dt, T, te, d, near)
    band = (min(near, far) - 4, max(near, far) + 4)
    bz = bursts[(bursts["price"] >= band[0]) & (bursts["price"] <= band[1]) & (bursts["direction"] == d)
                & (bursts["time"] >= te - 60) & (bursts["time"] <= te)]
    entry_px = int(r.entry_price)
    low_y = min(p["y"] for p in ps) if ps else entry_px * d
    bounce = (entry_px * d - low_y) * TICK
    inside = (entry_px - near) * d * TICK                       # + = back out of the zone our way
    i_t = int(np.searchsorted(dt.t, T))
    j10 = max(int(np.searchsorted(dt.t, T - 600, "right")) - 1, 0)
    move10 = (dt.px[j10] - dt.px[max(i_t - 1, 0)]) * d * TICK   # how far price came toward the zone
    kind = "demand" if d == 1 else "supply"
    where = (f"{abs(inside):.2f} pts inside the" if inside < 0 else
             f"{inside:.2f} pts back out of the" if inside > 0 else "at the edge of the")
    retest = len(ps) >= 2
    div = weaker = None
    if retest:
        a, b = ps[-2], ps[-1]
        div = b["cvd"] > a["cvd"]
        weaker = b["push_vol"] < 0.7 * max(a["push_vol"], 1)
    summary = (f"{'Long' if d == 1 else 'Short'} {where} {r.tf} {kind} zone, {te - T:.0f} s after price reached it "
               f"(test {int(r.zone_test) if r.zone_test == r.zone_test else 1} of this zone). Price came {move10:+.2f} pts "
               f"toward the zone in the 10 minutes before. {len(ps)} push{'es' if len(ps) != 1 else ''} into the zone so far; "
               f"price is {bounce:.2f} pts off the {ext_word}. In the last 5 s {us} took {100 * s5:.0f}% of the aggressive volume.")
    div_word = "higher low" if d == 1 else "lower high"
    check = [
        dict(ok=retest, item="Retest of the zone (a second push)",
             detail=(f"{len(ps)} pushes: price moved 2+ pts back out of the zone and returned" if retest else
                     "1 push, no retest: price has not moved 2 pts back out of the zone and returned")),
        dict(ok=div, item=f"CVD {div_word} on the retest",
             detail=("no retest to compare" if div is None else
                     f"CVD {'made a ' + div_word if div else 'did not make a ' + div_word} on the second push")),
        dict(ok=weaker, item="Weaker second push",
             detail=("no retest to compare" if weaker is None else
                     f"{them}' push into the zone: {ps[-1]['push_vol']} vs {ps[-2]['push_vol']} contracts the time before")),
        dict(ok=len(bz) > 0, item=f"Absorption at the zone ({them} absorbed)",
             detail=(f"{len(bz)} marker{'s' if len(bz) != 1 else ''} in the last minute, biggest {int(bz['contracts'].max())}"
                     if len(bz) else "no markers in the last minute")),
        dict(ok=bool(s5 >= 0.6 and s15 >= 0.55), item=f"{us.capitalize()} came in and held",
             detail=f"{us} {100 * s5:.0f}% of the last 5 s, {100 * s15:.0f}% of the last 15 s"),
        dict(ok=bool(s120 >= 0.45), item=f"{them.capitalize()} no longer in control",
             detail=f"{them} {100 * (1 - s120):.0f}% of the aggressive volume over the last 2 minutes"),
        dict(ok=bool(vol60 < 2 and rng60 < 4), item="Normal speed (not a fast or news market)",
             detail=f"last minute: {rng60:.2f}-pt range, volume {vol60:.1f}x normal"),
    ]
    return dict(summary=summary, checklist=check)


def explain_round(rnd: int) -> None:
    """Rewrite the bot's explanation on a round's clips in the owner's terms (python -m research.bot_review --explain N)."""
    rv = pd.read_csv(OUT / f"bot_review_round{rnd}.csv")
    files = {d.date().isoformat(): f for d, c, f in session_files()}
    R = S["replay"]
    for day, g in rv.groupby("day"):
        tr = load_trades(files[day], S)
        tr = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                               size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        dt = DayTrades(trades=tr)
        bu = find_bursts(tr["t"].to_numpy(), tr["price"].to_numpy(), tr["size"].to_numpy(), tr["side"].to_numpy(),
                         R["burst_seconds"], R["burst_min_mes_matched"])
        bu["direction"] = np.where(bu["colour"] == "purple", -1, 1)
        nps = lr.normal_per_second(dt, day)
        for r in g.itertuples():
            path = REPLAY_DIR / f"{r.id}.json"
            c = json.loads(path.read_text())
            v = owner_view(dt, bu, nps, r)
            c["bot"].update(summary=v["summary"], checklist=[dict(ok=(None if x["ok"] is None else bool(x["ok"])),
                                                                  item=x["item"], detail=x["detail"]) for x in v["checklist"]])
            path.write_text(json.dumps(c, separators=(",", ":")))
    print("explained", len(rv), "clips")


if __name__ == "__main__":
    import sys
    if "--explain" in sys.argv:
        explain_round(int(sys.argv[sys.argv.index("--explain") + 1]))
    else:
        main()
