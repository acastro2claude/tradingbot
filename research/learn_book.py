"""Heatmap test: does adding the order book let the learned bot see what the owner sees?

Owner (Nov 2026): "yea run that". Section 29 found the owner consistent, but by the bot's tape
measurements the owner's takes and skips look alike. The likely missing piece is the heatmap. All
marked clips have the full order book (2024 ES weeks, 2024 MES journal days, 16 MES days of 2026),
so here the same examples get order-book measurements at every decision point (the book as it
stood at the end of the second before; holding side = bids for a long, offers for a short):
  - book_line_zone / book_size_zone: the biggest resting line and the total resting size on the
    holding side in the zone (+-1 point), against the day's typical size per price
  - book_support / book_resistance: resting size on the holding side within 1 point behind price,
    and on the other side within 1 point ahead of it; book_near_balance: their balance
  - book_zone_change_30s / 120s: change of the holding side's resting size in the zone
    (+ = stacking or refilled, - = pulled or eaten)
  - book_pulled_share_30s / 120s: of the size that left the zone's holding side, the share cancelled
    rather than filled; book_refill_30s / 120s: size added per size filled
  - book_ahead_pulled_30s: size pulled on the other side within 2 points ahead of price
  - book_line_at_low: resting size on the holding side at the low since the test (+-1 tick)
  - book_touch_imbalance: best bid size against best ask size (our way)
Then the same cross-validation as research.learn_read (5 folds by day), with the tape only and
with tape + book, and the similar-clips check of research.learn_explain with each.
Writes research/results/journal/learn_book_summary.csv. Run:   python -m research.learn_book
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import research.learn_read as lr
from research.bot_week_study import FLAT_ET, SLIP_TICKS, et
from research.entry_read import day_trades_df
from research.exits import bot_exit
from research.journal_study import OUT
from research.level_study import DERIVED
from research.owner_rules import DayTrades

CACHE = DERIVED / "learn_book_points.parquet"
BOOK_FEATS = ["book_line_zone", "book_size_zone", "book_support", "book_resistance", "book_near_balance",
              "book_zone_change_30s", "book_zone_change_120s", "book_pulled_share_30s", "book_pulled_share_120s",
              "book_refill_30s", "book_refill_120s", "book_ahead_pulled_30s", "book_line_at_low", "book_touch_imbalance"]


def book_features(book, typ: float, reader, pts: np.ndarray, prices: np.ndarray, lows: np.ndarray, d: int,
                  near: int, far: int) -> pd.DataFrame:
    from research.orderbook import ASK, BID
    hold, opp = (book.bid, book.ask) if d == 1 else (book.ask, book.bid)
    hs, os_ = (BID, ASK) if d == 1 else (ASK, BID)
    lo_z, hi_z = min(near, far) - 4, max(near, far) + 4
    ncol = hold.shape[1]
    col = lambda p: int(np.clip(p - book.lo, 0, ncol - 1))  # noqa: E731
    rows = np.clip([book.row(t) for t in pts], 0, len(hold) - 1)
    out = {k: np.zeros(len(pts)) for k in BOOK_FEATS}
    zone_size = np.array([hold[r, col(lo_z):col(hi_z) + 1].sum() for r in rows], dtype=float)
    for i, (r, p, lw) in enumerate(zip(rows, prices, lows)):
        z = hold[r, col(lo_z):col(hi_z) + 1]
        out["book_line_zone"][i] = z.max() / typ if z.size else 0
        out["book_size_zone"][i] = zone_size[i] / typ
        b_lo, b_hi = (p - 4, p) if d == 1 else (p, p + 4)          # our side, within 1 point behind price
        a_lo_, a_hi_ = (p, p + 4) if d == 1 else (p - 4, p)        # the other side, within 1 point ahead
        behind = hold[r, col(b_lo):col(b_hi) + 1]
        ahead = opp[r, col(a_lo_):col(a_hi_) + 1]
        sup, res = float(behind.sum()), float(ahead.sum())
        out["book_support"][i], out["book_resistance"][i] = sup / typ, res / typ
        out["book_near_balance"][i] = (sup - res) / max(sup + res, 1)
        for w in (30, 120):
            r0 = max(r - w, 0)
            prev = hold[r0, col(lo_z):col(hi_z) + 1].sum()
            out[f"book_zone_change_{w}s"][i] = (zone_size[i] - prev) / typ
            added, pulled, filled = reader.flows(r0 + 1, r, hs, lo_z, hi_z)
            out[f"book_pulled_share_{w}s"][i] = pulled / max(pulled + filled, 1)
            out[f"book_refill_{w}s"][i] = added / max(filled, 1)
        a_lo, a_hi = (p, p + 8) if d == 1 else (p - 8, p)
        out["book_ahead_pulled_30s"][i] = reader.flows(max(r - 30, 0) + 1, r, os_, a_lo, a_hi)[1] / typ
        out["book_line_at_low"][i] = hold[r, col(lw - 1):col(lw + 1) + 1].sum() / typ
        bb = book.bid[r]
        ba = book.ask[r]
        nb, na = np.flatnonzero(bb > 0), np.flatnonzero(ba > 0)
        if len(nb) and len(na):
            qb, qa = float(bb[nb[-1]]), float(ba[na[0]])
            out["book_touch_imbalance"][i] = d * (qb - qa) / max(qb + qa, 1)
    return pd.DataFrame(out)


def build_points() -> pd.DataFrame:
    """Every decision point of every marked clip: tape features (research.learn_read) + book features."""
    if CACHE.exists():
        return pd.read_parquet(CACHE)
    from research.level_test_book import TestReader
    from research.orderbook import build_book, load_mbo
    from research.orderbook_study import typical_depth
    from research.replay_export import days, days_2026
    d = lr.marked_clips()
    files = {(x["market"], x["contract"], x["day"]): x for x in days() + days_2026()}
    parts = []
    for (market, contract, day), g in d.groupby(["market", "contract", "day"]):
        if (market, contract, day) not in files:
            print("no book for", market, contract, day, flush=True)
            continue
        f = files[(market, contract, day)]
        book = build_book(load_mbo(f["file"]), int(et(day, "08:00")), int(f["end"]))
        typ = typical_depth(book)
        reader = TestReader(book, typ, pd.DataFrame(columns=["direction", "price", "end_time", "contracts"]))
        tr = day_trades_df(market, contract, day)
        dt = DayTrades(trades=tr)
        b, nps = lr.day_bursts(tr, market), lr.normal_per_second(dt, day)
        for r in g.itertuples():
            far = int(r.lo) if r.direction == 1 else int(r.hi)
            x = lr.features(dt, b, nps, r.time, int(r.direction), int(r.edge), far, lr.TF_MIN.get(r.tf, 30),
                            float(r.zone_test), (r.hi - r.lo) * lr.TICK)
            lows = np.round((x["dist_near"] - x["bounce"]).to_numpy() * 4 + int(r.edge) * int(r.direction)).astype(int) * int(r.direction)
            bf = book_features(book, typ, reader, x["time"].to_numpy(), x["price"].to_numpy(), lows, int(r.direction),
                               int(r.edge), far)
            x = pd.concat([x.reset_index(drop=True), bf], axis=1)
            take = r.decision in ("long", "short")
            x["clip"], x["day"], x["market"], x["contract"], x["direction"] = r.clip_id, day, market, contract, int(r.direction)
            x["owner_takes"] = take
            x["owner_entry_time"] = r.entry_time if take else np.nan
            x["owner_entry_price"] = round(r.entry_price / lr.TICK) if take else np.nan
            x["test_time"], x["edge"] = r.time, int(r.edge)
            parts.append(x)
        print(day, market, len(g), flush=True)
        del book
    out = pd.concat(parts, ignore_index=True)
    out.to_parquet(CACHE, index=False)
    return out


def label(full: pd.DataFrame) -> pd.DataFrame:
    """The labelled examples, as in research.learn_read: enter-now around the press, not-yet before it."""
    x = full.copy()
    t_in = (x["owner_entry_time"] - x["test_time"]).clip(lower=0)
    pos = x["owner_takes"] & (x["t_since_test"] >= t_in - lr.L["positive_before_seconds"]) & (x["t_since_test"] <= t_in + lr.L["positive_after_seconds"])
    use = ~x["owner_takes"] | pos | (x["t_since_test"] < t_in - lr.L["wait_gap_seconds"])
    x["label"] = pos.astype(int)
    return x[use]


def run_cv(full: pd.DataFrame, data: pd.DataFrame, feats: list) -> pd.DataFrame:
    from sklearn.model_selection import GroupKFold
    clips = data.groupby("clip").agg(day=("day", "first"), owner=("owner_takes", "first")).reset_index()
    lr.FEATS = feats
    rows = []
    for k, (tr_i, te_i) in enumerate(GroupKFold(n_splits=lr.L["folds"]).split(clips, groups=clips["day"])):
        tr_c, te_c = set(clips.loc[tr_i, "clip"]), set(clips.loc[te_i, "clip"])
        m = lr.fit(data[data["clip"].isin(tr_c)])
        tp = full[full["clip"].isin(tr_c)]
        thr = float(pd.Series(m.predict_proba(tp[feats])[:, 1]).groupby(tp["clip"].to_numpy()).max()
                    .quantile(1 - clips.loc[tr_i, "owner"].mean()))
        fp = full[full["clip"].isin(te_c)].copy()
        fp["p"] = m.predict_proba(fp[feats])[:, 1]
        for c, g in fp.groupby("clip"):
            kk = lr.first_entry(g["p"].to_numpy(), thr)
            rows.append(dict(clip=c, bot_takes=kk is not None, owner_takes=bool(g["owner_takes"].iat[0]),
                             bot_time=float(g["time"].iat[kk]) if kk is not None else np.nan,
                             bot_price=int(g["price"].iat[kk]) if kk is not None else np.nan,
                             direction=int(g["direction"].iat[0]), market=g["market"].iat[0],
                             contract=g["contract"].iat[0], day=g["day"].iat[0]))
    cv = pd.DataFrame(rows)
    pts = []
    for (market, contract, day), g in cv.groupby(["market", "contract", "day"]):
        dt = DayTrades(trades=day_trades_df(market, contract, day))
        for r in g.itertuples():
            pts.append(bot_exit(dt, r.bot_time, int(r.bot_price) + r.direction * SLIP_TICKS, r.direction,
                                et(day, FLAT_ET), move=False)[0] * lr.TICK if r.bot_takes else np.nan)
    cv = cv.sort_values(["market", "contract", "day"], kind="stable").reset_index(drop=True)
    cv["bot_points"] = pts
    return cv


def similar_clips(full: pd.DataFrame, feats: list) -> float:
    at = full[full["t_since_test"] == 90].set_index("clip")
    z = ((at[feats] - at[feats].mean()) / at[feats].std().replace(0, 1)).to_numpy()
    dec, days = at["owner_takes"].to_numpy(), at["day"].to_numpy()
    same = []
    for i in range(len(z)):
        dist = np.sqrt(((z - z[i]) ** 2).sum(1))
        dist[days == days[i]] = np.inf
        same.append((dec[np.argsort(dist)[:5]] == dec[i]).mean())
    return float(np.mean(same))


def main() -> None:
    full = build_points()
    data = label(full)
    tape = [c for c in lr.feature_names(full) if not c.startswith("book_")]
    sets = {"tape only": tape, "tape + book": tape + BOOK_FEATS, "book only": BOOK_FEATS}
    out = []
    for name, feats in sets.items():
        cv = run_cv(full, data, feats)
        t = cv[cv["bot_takes"]]
        out.append(dict(features=name, clips=len(cv), owner_took=int(cv["owner_takes"].sum()),
                        agreement=(cv["bot_takes"] == cv["owner_takes"]).mean(), bot_trades=len(t),
                        bot_on_owner_takes=int(t["owner_takes"].sum()), bot_on_owner_skips=int((~t["owner_takes"]).sum()),
                        bot_win_rate=(t["bot_points"] > 0).mean(), bot_usd_per_trade=lr.usd(t["bot_points"]).mean(),
                        bot_usd_on_owner_skips=lr.usd(t.loc[~t["owner_takes"], "bot_points"]).mean(),
                        similar_clips_same_decision=similar_clips(full, feats)))
        print(out[-1], flush=True)
    s = pd.DataFrame(out)
    s.to_csv(OUT / "learn_book_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(s.round(3).to_string())


if __name__ == "__main__":
    main()
