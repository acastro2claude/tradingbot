"""Step 2: absorption = a burst that fails to push price through. Does it predict the turn?

STRATEGY.md section 2: the add-on flags a burst (100+ contracts at one price
within 0.01 s); the bot adds the absorption check: price does not trade more
than X ticks past the burst price within Y seconds (X, Y are TBD).

For every burst of 100+ and every (X, Y):
  - the check passes if price stayed within X ticks past the burst price for Y seconds
  - a trade is then taken in the absorbers' direction (purple -> short,
    green -> long) at the price a market order would get: a short fills like
    the next trade that hit the bid, a long like the next trade that lifted
    the ask. (Using the last trade price instead would hand the burst a free
    tick, because the last trade of a buy burst prints at the ask.)
  - it "works" if price moves 6 points from the fill before 3 points against.
Each is compared with random moments: every minute of the same session within
30 minutes, same direction, filled and scored the same way.

Needs the raw regular-hours trades (data/raw/trades_rth) and
research.absorption_study's outputs.
Run from the repo root:   python -m research.absorption_check_study
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from research.absorption_study import STEP2_OUT, session_files
from research.level_study import DERIVED
from research.market_data import epoch_seconds, load_trades
from research.outcomes import event_outcomes
from research.paths import trade_path
from research.settings import load_settings, ticks

CHECK_SECONDS = [0, 5, 10, 30, 60]       # 0 = no check: enter right after the burst
CHECK_TICKS = [0, 1, 2, 4]               # how far past the burst price price may trade
TARGET_POINTS = 6
LEVEL_COLS = ["recent uncovered POC", "POC cluster (2+ within 2 pts)",
              "S/D zone 15 min", "S/D zone 30 min", "S/D zone 60 min"]


def market_fill(tt: np.ndarray, px: np.ndarray, side: np.ndarray, when: np.ndarray,
                direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Time and price a market order would get at `when`: a short (-1) fills like the
    next sell-aggressor trade (at the bid), a long (+1) like the next buy-aggressor trade.
    NaN time where no such trade follows in the session."""
    ft = np.full(len(when), np.nan)
    fp = np.zeros(len(when), dtype=np.int64)
    for d, code in ((-1, "A"), (1, "B")):
        m = direction == d
        st, sp = tt[side == code], px[side == code]
        j = np.searchsorted(st, when[m], "left")
        ok = j < len(st)
        idx = np.flatnonzero(m)[ok]
        ft[idx] = st[j[ok]]
        fp[idx] = sp[j[ok]]
    return ft, fp


def score(p, t, tt, px, side, when, direction, s) -> pd.DataFrame:
    brk = ticks(s["bot"]["stop_buffer_points"], s)
    horizon = s["level_study"]["outcome_horizon_minutes"] * 60.0
    ft, fp = market_fill(tt, px, side, when, direction)
    ok = ~np.isnan(ft)
    out = pd.DataFrame({"fill_time": ft, "fill_price": fp, "mfe": np.nan, "broke": False})
    if ok.any():
        o = event_outcomes(p, t, ft[ok], fp[ok], direction[ok], brk, horizon)
        out.loc[ok, "mfe"] = o["mfe"].to_numpy()
        out.loc[ok, "broke"] = o["broke"].to_numpy()
    return out


def score_events(ev: pd.DataFrame, s: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bursts scored for each check time, plus random minutes scored the same way."""
    files = {sess: f for sess, _, f in session_files()}
    parts, base = [], []
    for sess, g in ev.groupby("session"):
        tr = load_trades(files[sess], s)
        tt = epoch_seconds(tr["ts"])
        px = tr["price"].to_numpy() + int(g["offset_ticks"].iat[0])
        side = tr["side"].to_numpy()
        p, t = trade_path(px, tt)
        d = g["direction"].to_numpy()
        out = pd.DataFrame(index=g.index)
        for y in CHECK_SECONDS:
            o = score(p, t, tt, px, side, g["end_time"].to_numpy() + y, d, s)
            out[f"mfe_{y}s"] = o["mfe"].to_numpy()
            out[f"broke_{y}s"] = o["broke"].to_numpy()
            out[f"fill_time_{y}s"] = o["fill_time"].to_numpy()
            out[f"fill_vs_burst_{y}s"] = (o["fill_price"].to_numpy() - g["price"].to_numpy()) * d
        parts.append(out)
        minutes = np.arange(np.ceil(tt[0] / 60) * 60, tt[-1] - 60, 60.0)
        for dd in (-1, 1):
            o = score(p, t, tt, px, side, minutes, np.full(len(minutes), dd), s)
            base.append(pd.DataFrame({"session": sess, "time": o["fill_time"], "direction": dd,
                                      "mfe": o["mfe"], "broke": o["broke"]}).dropna(subset=["time"]))
    return ev.join(pd.concat(parts)), pd.concat(base, ignore_index=True)


def matched_random(ev: pd.DataFrame, base: pd.DataFrame, when_col: str, window: float = 1800.0) -> np.ndarray:
    """Worked rate of random minutes in the same session and direction within +/- window seconds."""
    tgt = ticks(TARGET_POINTS)
    b = base.copy()
    dec = (b["mfe"] >= tgt) | b["broke"]
    b = b[dec]
    b["w"] = (b["mfe"] >= tgt).astype(float)
    out = np.full(len(ev), np.nan)
    pos = {k: i for i, k in enumerate(ev.index)}
    for (sess, d), g in ev.groupby(["session", "direction"]):
        bb = b[(b["session"] == sess) & (b["direction"] == d)].sort_values("time")
        if bb.empty:
            continue
        bt = bb["time"].to_numpy()
        cw = np.concatenate([[0.0], np.cumsum(bb["w"].to_numpy())])
        w = g[when_col].to_numpy()
        lo, hi = np.searchsorted(bt, w - window, "left"), np.searchsorted(bt, w + window, "right")
        rate = np.where(hi - lo >= 10, (cw[hi] - cw[lo]) / np.maximum(hi - lo, 1), np.nan)
        out[[pos[i] for i in g.index]] = rate
    return out


def summarise(ev: pd.DataFrame, base: pd.DataFrame, s: dict) -> pd.DataFrame:
    ls = s["level_study"]
    rng = np.random.default_rng(ls["random_seed"])
    B = ls["bootstrap_samples"]
    tgt = ticks(TARGET_POINTS, s)
    rows = []

    def add(label: dict, g: pd.DataFrame, y: int):
        w = g[f"mfe_{y}s"] >= tgt
        dec = w | g[f"broke_{y}s"]
        g = g[dec].assign(w=w[dec].astype(float))
        g = g[g[f"exp_{y}s"].notna()]
        row = dict(label, tests=len(g), sessions=g["session"].nunique(),
                   worked=g["w"].mean() if len(g) else np.nan,
                   random_same_time=g[f"exp_{y}s"].mean() if len(g) else np.nan)
        if len(g) >= 20:
            per = g.groupby("session").agg(w=("w", "sum"), e=(f"exp_{y}s", "sum"), n=("w", "size"))
            idx = rng.integers(0, len(per), size=(B, len(per)))
            d = (per["w"].to_numpy()[idx].sum(1) - per["e"].to_numpy()[idx].sum(1)) / per["n"].to_numpy()[idx].sum(1)
            row.update(edge=(per["w"].sum() - per["e"].sum()) / per["n"].sum(),
                       edge_low=np.percentile(d, 2.5), edge_high=np.percentile(d, 97.5))
        rows.append(row)

    for y in CHECK_SECONDS:
        for x in (CHECK_TICKS if y > 0 else [None]):
            passed = np.ones(len(ev), bool) if y == 0 else (ev[f"against_{y}s"] <= x).to_numpy()
            for colour in ("purple", "green", "both"):
                m = passed & ((ev["colour"] == colour) if colour != "both" else True)
                add(dict(check_seconds=y, check_ticks=x, colour=colour, where="anywhere",
                         passed_share=passed.mean()), ev[m], y)
            for lvl in LEVEL_COLS:
                m = passed & ev[lvl].fillna(False).to_numpy().astype(bool)
                add(dict(check_seconds=y, check_ticks=x, colour="both", where=lvl,
                         passed_share=passed.mean()), ev[m], y)
    return pd.DataFrame(rows)


def main() -> None:
    s = load_settings()
    t0 = time.time()
    ev = pd.read_parquet(DERIVED / "absorption_events.parquet")
    ev, base = score_events(ev, s)
    for y in CHECK_SECONDS:
        ev[f"exp_{y}s"] = matched_random(ev, base, f"fill_time_{y}s")
    ev.to_parquet(DERIVED / "absorption_check_events.parquet")
    base.to_parquet(DERIVED / "absorption_check_baseline.parquet")
    out = summarise(ev, base, s)
    Path(STEP2_OUT).mkdir(parents=True, exist_ok=True)
    out.to_csv(STEP2_OUT / "absorption_check_results.csv", index=False)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
