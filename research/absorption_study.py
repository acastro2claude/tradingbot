"""Step 2, first test: does price go the expected way after an absorption burst?

Purple burst (aggressive buyers absorbed at price P) -> expect DOWN.
Green burst (aggressive sellers absorbed at P)      -> expect UP.
"Worked" = price moved 6 points the expected way from P before trading 3
points the other way past P (the bot's stop buffer). Chance gives 33.3%.

Each burst is compared with random moments: every minute of the same session
and hour, in the same direction, scored the same way from the last price.
Splits: at a level or not (owner's recent uncovered POCs, POC clusters,
supply and demand zones), and the hold check (price didn't trade more than
X ticks past P within Y seconds; X and Y are TBD in STRATEGY.md).

Needs: research.download_backtest_trades (trades, regular hours) and
research.level_study + research.supply_demand_study (sessions, POCs, zones).
Run from the repo root:   python -m research.absorption_study
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from research.absorption import find_bursts
from research.level_study import DERIVED, prepare
from research.levels import cluster_pocs
from research.market_data import epoch_seconds, load_trades
from research.outcomes import event_outcomes
from research.paths import trade_path
from research.recent_poc_study import recent_poc_labels
from research.settings import load_settings, ticks

TRADES_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "trades_rth"
STEP2_OUT = Path(__file__).resolve().parent / "results" / "step2"


def session_files() -> list[tuple[pd.Timestamp, str, Path]]:
    out = []
    for f in sorted(TRADES_DIR.glob("*.dbn.zst")):
        contract, day = f.name.split(".")[0].split("_")
        out.append((pd.Timestamp(day), contract, f))
    return out


def level_prices(sessions: pd.DataFrame, session: pd.Timestamp, s: dict) -> dict[str, np.ndarray]:
    """Level prices (adjusted ticks) available at the start of a session."""
    bot = s["bot"]
    usable = list(sessions.index[sessions["use"]])
    k = usable.index(session)
    pocs20 = sessions.loc[usable[k - bot["poc_lookback_sessions"]:k], "poc_adj"].to_numpy()
    rec = recent_poc_labels(sessions, pd.Index([session]), bot["recent_poc_lookback_sessions"])
    return {
        "recent uncovered POC": rec.loc[~rec["covered"], "price"].to_numpy(),
        "POC cluster (2+ within 2 pts)": np.array([g.price for g in cluster_pocs(pocs20, 8) if g.count >= 2]),
    }


def near(prices: np.ndarray, levels: np.ndarray, tol: int) -> np.ndarray:
    if len(levels) == 0:
        return np.zeros(len(prices), dtype=bool)
    return (np.abs(prices[:, None] - levels[None, :]) <= tol).any(axis=1)


def in_matching_zone(times, prices, dirs, zones: pd.DataFrame, tol: int) -> np.ndarray:
    """Burst inside (within tol of) a live zone of the right kind: purple (expect down) needs supply."""
    if zones.empty:
        return np.zeros(len(times), dtype=bool)
    live = (zones["created"].to_numpy()[None, :] <= times[:, None]) & (zones["dies"].to_numpy()[None, :] > times[:, None])
    inside = ((zones["low"].to_numpy()[None, :] - tol <= prices[:, None])
              & (prices[:, None] <= zones["high"].to_numpy()[None, :] + tol))
    kind_ok = np.where(dirs[:, None] == -1, (zones["kind"] == "supply").to_numpy()[None, :],
                       (zones["kind"] == "demand").to_numpy()[None, :])
    return (live & inside & kind_ok).any(axis=1)


def main() -> None:
    s = load_settings()
    ls, bot = s["level_study"], s["bot"]
    window, min_size = bot["absorption_burst_seconds"], bot["absorption_burst_contracts"]
    brk = ticks(bot["stop_buffer_points"], s)
    horizon = ls["outcome_horizon_minutes"] * 60.0
    checks = [5, 10, 30, 60]
    tol = ticks(2, s)                         # "at a level": burst within 2 points of it
    _, sessions, _ = prepare(s)
    zones = {tf: pd.read_csv(DERIVED / f"sd_zones_{tf}min_last_chop_candle_body.csv")
             for tf in ls["sd_timeframes_minutes"]}

    all_bursts, events, base = [], [], []
    t0 = time.time()
    files = session_files()
    for n, (sess, contract, f) in enumerate(files):
        if sess not in sessions.index or sessions.loc[sess, "contract"] != contract:
            print(f"skip {f.name}: not the front contract in the session table")
            continue
        off = int(sessions.loc[sess, "offset_ticks"])
        tr = load_trades(f, s)
        tt = epoch_seconds(tr["ts"])
        px = tr["price"].to_numpy() + off
        b = find_bursts(tt, px, tr["size"].to_numpy(), tr["side"].to_numpy(), window, 50)
        b["session"], b["contract"], b["offset_ticks"] = sess, contract, off
        all_bursts.append(b)

        p, t = trade_path(px, tt)
        ev = b[b["contracts"] >= min_size].copy()
        ev["direction"] = np.where(ev["colour"] == "purple", -1, 1)
        o = event_outcomes(p, t, ev["end_time"], ev["price"], ev["direction"], brk, horizon, checks)
        ev = pd.concat([ev.reset_index(drop=True), o], axis=1)
        if sessions.loc[sess, "use"]:
            lv = level_prices(sessions, sess, s)
            for name, levels in lv.items():
                ev[name] = near(ev["price"].to_numpy(), levels, tol)
            for tf, z in zones.items():
                ev[f"S/D zone {tf} min"] = in_matching_zone(ev["end_time"].to_numpy(), ev["price"].to_numpy(),
                                                          ev["direction"].to_numpy(), z, tol)
        events.append(ev)

        # random moments: every minute, both directions, from the last traded price
        minutes = np.arange(np.ceil(tt[0] / 60) * 60, tt[-1] - 60, 60.0)
        last = px[np.searchsorted(tt, minutes, "right") - 1]
        for d in (-1, 1):
            ob = event_outcomes(p, t, minutes, last, np.full(len(minutes), d), brk, horizon)
            base.append(pd.DataFrame({"session": sess, "time": minutes, "direction": d,
                                      "mfe": ob["mfe"], "broke": ob["broke"]}))
        if (n + 1) % 10 == 0:
            print(f"  {n + 1}/{len(files)} sessions, {time.time() - t0:.0f}s")

    bursts = pd.concat(all_bursts, ignore_index=True)
    events = pd.concat(events, ignore_index=True)
    base = pd.concat(base, ignore_index=True)
    STEP2_OUT.mkdir(parents=True, exist_ok=True)
    DERIVED.mkdir(parents=True, exist_ok=True)
    bursts.to_csv(STEP2_OUT / "bursts_50plus.csv.gz", index=False)   # compact; reusable without re-buying
    events.to_parquet(DERIVED / "absorption_events.parquet")
    base.to_parquet(DERIVED / "absorption_baseline.parquet")
    print(f"{len(files)} sessions, {len(bursts):,} bursts of 50+, {len(events):,} of {min_size}+, "
          f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
