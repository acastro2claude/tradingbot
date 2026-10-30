"""What the order book showed at the owner's entries (build step 3, first look).

Uses the full MES book (Databento MBO) rebuilt for the 20 busiest journal days
(research.download_journal_orderbook). For every journal trade on those days,
and for random moments in the same time windows (random direction), it reads
the book just before the moment, the way it would look on Bookmap:

  "wall" side = the resting orders the trade leans on (asks above a short,
  bids below a long); "path" side = the orders in the way of the target.
  - size resting within 1, 2 and 5 points of the touch, both sides
  - lines: the biggest single price within 2 or 5 points, as a multiple of
    the day's typical size per price near the market
  - how long the biggest wall price within 2 points had been there
  - refill: contracts filled in the last 2 minutes at the wall prices within
    1 point, against the most ever shown there (above 1 = it kept refilling)
  - pulls and adds on each side within 2 points over the last 30 seconds
  - the move into the moment: how far price came toward the entry over the
    last 5 and 30 seconds, 2 and 5 minutes

The owner usually enters once price has started to turn their way (median
0.75 points in the last 30 seconds), and that move by itself changes what the
book looks like near the price. So the entries are
also compared with random moments from the same day whose move over the last
5 seconds, 30 seconds and 2 minutes was closest ("matched").

Each moment also gets a simple outcome: enter at the bid (short) or ask (long),
then did price reach 4 points in favour before 2 points against, within 30
minutes (the owner's median win and loss). For the owner's trades the actual
points from the journal are kept too.

Run from the repo root:   python -m research.orderbook_study
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.journal_study import OUT
from research.level_study import RAW
from research.orderbook import ASK, BID, Book, build_book, check_book, load_mbo
from research.settings import load_settings

MBO_DIR = RAW / "journal_mbo"
DAYS = 20
LOOKBACK_S = 1800                # book history kept before each day's window
RANDOM_PER_DAY = 300
MATCHES = 5                      # random moments matched to each entry
MATCH_ON = ("move_5s", "move_30s", "move_2m")   # the recent move, in ticks toward the entry
TARGET_TICKS, STOP_TICKS, HORIZON_S = 16, 8, 1800
LINE_MULTS = (2.0, 3.0)          # a "line": at least this many times the day's typical size per price


def typical_depth(book: Book, levels: int = 10, every: int = 10) -> float:
    """Median resting size per price within `levels` of the touch, both sides, sampled every few seconds."""
    vals = []
    for r in range(0, len(book.bid), every):
        bb, ba = book.best(r)
        if bb < 0 or ba < 0:
            continue
        vals.append(book.bid[r, bb - levels + 1 - book.lo: bb + 1 - book.lo])
        vals.append(book.ask[r, ba - book.lo: ba + levels - book.lo])
    v = np.concatenate(vals)
    return float(np.median(v[v > 0]))


def outward(book: Book, r: int, side: int, touch: int, n: int) -> np.ndarray:
    """Resting size at the n prices from the touch outward (away from the market) on one side."""
    if side == ASK:
        return book.ask[r, touch - book.lo: touch - book.lo + n]
    return book.bid[r, touch - n + 1 - book.lo: touch + 1 - book.lo][::-1]


class Day:
    """One day's rebuilt book plus fast lookups into its flows and trades."""

    def __init__(self, book: Book):
        self.book = book
        self.typ = typical_depth(book)
        self.f_sec = book.flows["sec"].to_numpy()
        self.f_side = book.flows["side"].to_numpy()
        self.f_price = book.flows["price"].to_numpy()
        self.f_vals = book.flows[["added", "pulled", "filled"]].to_numpy()
        self.t = book.trades["t"].to_numpy()
        self.px = book.trades["price"].to_numpy()

    def flows(self, r: int, seconds: int, side: int, prices: np.ndarray) -> np.ndarray:
        """Added, pulled, filled per price in `prices` over the `seconds` before grid row r (inclusive)."""
        i, j = np.searchsorted(self.f_sec, [r - seconds + 1, r + 1])
        sel = (self.f_side[i:j] == side)
        out = np.zeros((len(prices), 3), dtype=np.int64)
        idx = {p: k for k, p in enumerate(prices)}
        for p, v in zip(self.f_price[i:j][sel], self.f_vals[i:j][sel]):
            k = idx.get(p)
            if k is not None:
                out[k] += v
        return out

    def price_before(self, t: float) -> int | None:
        i = np.searchsorted(self.t, t, "left")
        return int(self.px[i - 1]) if i > 0 else None


def book_features(day: Day, t: float, d: int) -> dict | None:
    book, typ = day.book, day.typ
    r = book.row(t)
    if r < 300:
        return None
    bb, ba = book.best(r)
    if bb < 0 or ba < 0:
        return None
    wall_side, path_side = (ASK, BID) if d == -1 else (BID, ASK)
    w_touch, p_touch = (ba, bb) if d == -1 else (bb, ba)
    wall, path = outward(book, r, wall_side, w_touch, 20), outward(book, r, path_side, p_touch, 20)
    out = dict(spread_ticks=ba - bb, fill_price=p_touch)   # a market order fills on the path side's touch

    now = day.price_before(t)
    for name, secs in (("move_5s", 5), ("move_30s", 30), ("move_2m", 120), ("move_5m", 300)):
        then = day.price_before(t - secs)
        out[name] = (now - then) * -d if now is not None and then is not None else 0   # ticks toward the entry

    for pts, n in ((1, 4), (2, 8), (5, 20)):
        out[f"wall_{pts}pt"] = int(wall[:n].sum())
        out[f"path_{pts}pt"] = int(path[:n].sum())
    out["imbalance_2pt"] = out["wall_2pt"] / max(out["wall_2pt"] + out["path_2pt"], 1)
    out["imbalance_5pt"] = out["wall_5pt"] / max(out["wall_5pt"] + out["path_5pt"], 1)
    for pts, n in ((2, 8), (5, 20)):
        out[f"wall_line_{pts}pt"] = wall[:n].max() / typ
        out[f"path_line_{pts}pt"] = path[:n].max() / typ

    # how long the biggest wall price within 2 points had held at least half its current size
    k = int(np.argmax(wall[:8]))
    price = w_touch - k if wall_side == BID else w_touch + k
    col = (book.bid if wall_side == BID else book.ask)[: r + 1, price - book.lo]
    weak = np.nonzero(col < 0.5 * col[-1])[0]
    out["wall_line_age_s"] = int(r - weak[-1]) if len(weak) else int(r + 1)

    # refill: filled in the last 2 minutes at wall prices within 1 point vs the most ever shown there
    sign = 1 if wall_side == ASK else -1
    near = w_touch + sign * np.arange(4)
    g = book.ask if wall_side == ASK else book.bid
    shown = g[max(r - 120, 0): r + 1, near - book.lo].max(axis=0)
    filled = day.flows(r, 120, wall_side, near)[:, 2]
    out["wall_filled_2min_1pt"] = int(filled.sum())
    out["refill_ratio"] = float((filled / np.maximum(shown, 1)).max())

    for name, side, touch in (("wall", wall_side, w_touch), ("path", path_side, p_touch)):
        s = 1 if side == ASK else -1
        fw = day.flows(r, 30, side, touch + s * np.arange(8)).sum(axis=0)
        out[f"{name}_added_30s"] = int(fw[0])
        out[f"{name}_pulled_30s"] = int(fw[1])
    return out


def fixed_outcome(day: Day, t: float, entry: int, d: int) -> str:
    """'target' if price reaches 4 points in favour before 2 against within 30 minutes, else 'stop'/'neither'."""
    i, j = np.searchsorted(day.t, [t, t + HORIZON_S], "right")
    move = (day.px[i:j] - entry) * d
    hit_t = np.nonzero(move >= TARGET_TICKS)[0]
    hit_s = np.nonzero(move <= -STOP_TICKS)[0]
    a = hit_t[0] if len(hit_t) else np.inf
    b = hit_s[0] if len(hit_s) else np.inf
    return "neither" if a == b == np.inf else ("target" if a < b else "stop")


def day_moments(plan_row, trades_today: pd.DataFrame, rng) -> pd.DataFrame | None:
    f = MBO_DIR / f"{plan_row.mes}_{plan_row.day}_mbo.dbn.zst"
    if not f.exists():
        return None
    start = int(pd.Timestamp(plan_row.start, tz="UTC").timestamp())
    end = int(pd.Timestamp(plan_row.end, tz="UTC").timestamp())
    day = Day(build_book(load_mbo(f), start - LOOKBACK_S, end))     # the data stops at `end`
    chk = check_book(day.book)
    rows = []
    for tr in trades_today.itertuples():
        t = tr.entry_time.timestamp()
        d = 1 if tr.side == "long" else -1
        fe = book_features(day, t, d)
        if fe is None:
            continue
        rows.append(dict(kind="owner", day=plan_row.day, t=t, direction=d, points=tr.points,
                         outcome_2_4=fixed_outcome(day, t, fe["fill_price"], d), **fe))
    for t in np.sort(rng.uniform(start + 60, end - 600, RANDOM_PER_DAY)):
        d = int(rng.choice([-1, 1]))
        fe = book_features(day, t, d)
        if fe is None:
            continue
        rows.append(dict(kind="random", day=plan_row.day, t=t, direction=d, points=np.nan,
                         outcome_2_4=fixed_outcome(day, t, fe["fill_price"], d), **fe))
    out = pd.DataFrame(rows)
    out["typical_size"] = day.typ
    print(f"{plan_row.day} {plan_row.mes}: {chk} typical size per price {day.typ:.0f}, "
          f"{(out['kind'] == 'owner').sum()} trades", flush=True)
    return out


def matched_controls(m: pd.DataFrame) -> pd.DataFrame:
    """For each owner entry, the MATCHES random moments of the same day with the closest recent move."""
    own, rnd = m[m["kind"] == "owner"], m[m["kind"] == "random"]
    cols = list(MATCH_ON)
    scale = rnd[cols].std().to_numpy()
    picks = []
    for i, e in own.iterrows():
        pool = rnd[rnd["day"] == e["day"]]
        dist = np.sqrt((((pool[cols].to_numpy() - e[cols].to_numpy(float)) / scale) ** 2).sum(axis=1))
        sel = pool.iloc[np.argsort(dist)[:MATCHES]].copy()
        sel["entry_id"] = i
        sel["weight"] = 1.0 / len(sel)
        picks.append(sel)
    return pd.concat(picks)


def readings(df: pd.DataFrame) -> pd.DataFrame:
    """Yes/no book readings per moment (1 = present)."""
    r = {}
    for k in LINE_MULTS:
        r[f"line {k:g}x+ on the wall side within 2 pts"] = df["wall_line_2pt"] >= k
        r[f"line {k:g}x+ on the wall side within 5 pts"] = df["wall_line_5pt"] >= k
        r[f"line {k:g}x+ in the path within 2 pts"] = df["path_line_2pt"] >= k
    r["more resting on the wall side than the path (2 pts)"] = df["imbalance_2pt"] > 0.5
    r["more resting on the wall side than the path (5 pts)"] = df["imbalance_5pt"] > 0.5
    r["wall refilled (filled > most shown, last 2 min, 1 pt)"] = df["refill_ratio"] >= 1
    r["wall refilled 2x+ (last 2 min, 1 pt)"] = df["refill_ratio"] >= 2
    r["wall pulled more than added (last 30 s, 2 pts)"] = df["wall_pulled_30s"] > df["wall_added_30s"]
    r["path pulled more than added (last 30 s, 2 pts)"] = df["path_pulled_30s"] > df["path_added_30s"]
    r["price came 2+ pts toward the entry in the last 2 min"] = df["move_2m"] >= 8
    r["2-pt stop / 4-pt target: reached target first"] = df["outcome_2_4"] == "target"
    return pd.DataFrame(r).astype(float)


def summary_tables(m: pd.DataFrame, matched: pd.DataFrame, rng) -> tuple[pd.DataFrame, pd.DataFrame]:
    own, rnd = m[m["kind"] == "owner"], m[m["kind"] == "random"]
    win, lose = own[own["points"] > 0], own[own["points"] <= 0]
    ro, rm = readings(own), readings(matched)
    w = matched["weight"].to_numpy()
    share = pd.DataFrame({
        "your entries": ro.mean(),
        "random, matched on the move in": (rm.mul(w, axis=0).sum() / w.sum()),
        "random, same windows": readings(rnd).mean(),
        "your winners": readings(win).mean(),
        "your losers": readings(lose).mean(),
    })
    # 95% range for (entries - matched) by resampling whole days
    days = own["day"].unique()
    diffs = []
    for _ in range(2000):
        pick = rng.choice(days, len(days))
        o = pd.concat([ro[own["day"].to_numpy() == d] for d in pick])
        mm = pd.concat([rm[matched["day"].to_numpy() == d].mul(w[matched["day"].to_numpy() == d], axis=0) for d in pick])
        ww = np.concatenate([w[matched["day"].to_numpy() == d] for d in pick])
        diffs.append(o.mean() - mm.sum() / ww.sum())
    diffs = pd.DataFrame(diffs)
    share["entries minus matched: low"] = diffs.quantile(0.025)
    share["entries minus matched: high"] = diffs.quantile(0.975)
    counts = pd.Series({"your entries": len(own), "random, matched on the move in": len(matched) // MATCHES,
                        "random, same windows": len(rnd), "your winners": len(win), "your losers": len(lose)},
                       name="moments")
    share = pd.concat([share, counts.to_frame().T])

    cols = ["move_5s", "move_30s", "move_2m", "wall_2pt", "path_2pt", "imbalance_2pt", "wall_line_2pt", "path_line_2pt",
            "wall_line_5pt", "wall_line_age_s", "wall_filled_2min_1pt", "refill_ratio",
            "wall_pulled_30s", "wall_added_30s", "path_pulled_30s", "path_added_30s"]
    med = pd.DataFrame({"your entries": own[cols].median(), "random, matched on the move in": matched[cols].median(),
                        "random, same windows": rnd[cols].median(), "your winners": win[cols].median(),
                        "your losers": lose[cols].median()})
    return share, med


def edge_by_reading(rnd: pd.DataFrame) -> pd.DataFrame:
    """Random moments: 2/4 target-first rate with and without each book reading."""
    r = readings(rnd)
    hit = r.pop("2-pt stop / 4-pt target: reached target first").astype(bool)
    r["wall side heavier (2 pts, 60%+)"] = (rnd["imbalance_2pt"] >= 0.6).astype(float)
    r["path side heavier (2 pts, 60%+)"] = (rnd["imbalance_2pt"] <= 0.4).astype(float)
    r["wall line 2x+ within 2 pts and refilled"] = ((rnd["wall_line_2pt"] >= 2) & (rnd["refill_ratio"] >= 1)).astype(float)
    rows = []
    for name in r.columns:
        mask = r[name].astype(bool).to_numpy()
        rows.append(dict(reading=name, moments_with=int(mask.sum()), target_first_with=hit[mask].mean(),
                         moments_without=int((~mask).sum()), target_first_without=hit[~mask].mean()))
    return pd.DataFrame(rows)


def main() -> None:
    s = load_settings()
    rng = np.random.default_rng(s["level_study"]["random_seed"])
    plan = pd.read_csv(OUT / "orderbook_price_plan.csv").head(DAYS)
    t = pd.read_csv(OUT / "journal_trades.csv")
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c], utc=True)
    t = t[t["closed"]].copy()
    t["day"] = t["entry_time"].dt.tz_convert("America/New_York").dt.date.astype(str)
    t["mes"] = "MES" + t["contract"].str.extract(r"([HMUZ])\d(\d)$").agg("".join, axis=1)

    parts = []
    for r in plan.itertuples():
        today = t[(t["day"] == r.day) & (t["mes"] == r.mes)]
        m = day_moments(r, today, rng)
        if m is not None:
            parts.append(m)
    m = pd.concat(parts, ignore_index=True)
    m.to_csv(OUT / "orderbook_moments.csv", index=False)
    matched = matched_controls(m)
    share, med = summary_tables(m, matched, rng)
    share.to_csv(OUT / "orderbook_summary.csv")
    med.to_csv(OUT / "orderbook_medians.csv")
    edge = edge_by_reading(m[m["kind"] == "random"])
    edge.to_csv(OUT / "orderbook_edge.csv", index=False)
    own = m[m["kind"] == "owner"]
    print(f"\nyour trades on these days: {len(own)}, win rate {(own['points'] > 0).mean():.0%}, "
          f"average {own['points'].mean():+.2f} pts per contract")
    pd.set_option("display.width", 250)
    print((share.iloc[:-1] * 100).round(0).to_string())
    print(share.iloc[-1].to_string())
    print(med.round(2).to_string())
    print(edge.round(3).to_string())


if __name__ == "__main__":
    main()
