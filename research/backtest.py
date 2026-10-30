"""Step 2 backtest: the full setup from STRATEGY.md sections 2-4, on trade data.

Short (mirror for long):
  1. A purple burst (aggressive buyers absorbed, 100+ contracts at one price
     within 0.01 s) within `level_tolerance` of a level.
  2. Mostly seller absorption at the level: in the last few minutes, purple
     burst contracts within the tolerance of the level >= green ones.
  3. Optional hold check: price stays within X ticks above the burst price for Y s.
  4. Entry: market order right after (fills like the next trade that hit the bid),
     minus slippage.
  5. Stop: 3 points beyond the level. Risk at most 7 points (max entry distance 4).
  6. First target (9 of 10 contracts): the top of the last 1-minute chop before
     the sharp move into the level. Needs at least 2x the stop distance.
  7. Runner (1 contract): the lowest price from the start of that chop to the
     entry. After the first target, its stop moves to 6 points from entry
     (to breakeven if that isn't past the current price).
  8. One trade a day. No entries in news blackouts; flat 5 minutes before a
     release. No entries after the cutoff; flat at the end of the data.
  9. Costs: slippage on market entries and stop exits (targets are limit
     orders that fill when price trades 1 tick through), plus commission.

Levels (variants): the owner's recent uncovered POCs (1e), 15-minute supply and
demand zones (1b, owner's drawing), either of those, POC clusters (1a), or no
level at all (the burst price itself is the level).

Run from the repo root:   python -m research.backtest
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from research.absorption_study import STEP2_OUT, session_files
from research.level_study import DERIVED, prepare
from research.levels import cluster_pocs
from research.market_data import NY, epoch_seconds, load_trades
from research.paths import trade_path
from research.recent_poc_study import recent_poc_labels
from research.settings import load_settings, ticks
from research.supply_demand import find_zones, make_candles

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- one trade

@dataclass
class TradeResult:
    exit_reason: str          # "stop", "first target + runner target", "first target + runner stop", "flatten", ...
    first_target_hit: bool
    points: float             # total points over all contracts, before commission
    exit_time: float


def _first(cond: np.ndarray) -> int:
    """Index of the first True, or len(cond) if none."""
    return int(np.argmax(cond)) if cond.any() else len(cond)


def simulate_trade(p: np.ndarray, t: np.ndarray, i0: int, entry: int, d: int, stop: int,
                   first_target: int, runner_target: int, flatten_t: float, slip: int,
                   contracts: int = 10, first_contracts: int = 9, runner_lock: int = 24) -> TradeResult:
    """Walk the price path (ticks, 1 tick per step) from index i0. d = -1 short, +1 long.

    Targets are limit orders: filled once price trades 1 tick through them.
    Stops and the end-of-day exit are market orders: `slip` ticks worse.
    """
    tick_size = 0.25
    seg_p, seg_t = p[i0:], t[i0:]
    hit_stop = _first(seg_p * d <= stop * d)                         # short: price >= stop
    hit_ft = _first((seg_p - first_target) * d >= 1)                # short: price <= ft - 1
    hit_end = _first(seg_t >= flatten_t)
    first = min(hit_stop, hit_ft, hit_end)
    if first == len(seg_p):                                         # data ran out: flatten at the last price
        first, hit_end = len(seg_p) - 1, len(seg_p) - 1
    if first == hit_stop and hit_stop <= hit_ft:
        pts = (entry - (stop - d * slip)) * -d * contracts
        return TradeResult("stop", False, pts * tick_size, float(seg_t[first]))
    if first == hit_end and hit_end < hit_ft:
        px = seg_p[first] - d * slip
        return TradeResult("flatten", False, (px - entry) * d * contracts * tick_size, float(seg_t[first]))

    # first target filled for most contracts
    pts = (first_target - entry) * d * first_contracts
    runner = contracts - first_contracts
    if runner == 0 or (runner_target - first_target) * d <= 0:
        pts += (first_target - entry) * d * runner
        return TradeResult("first target (no runner)", True, pts * tick_size, float(seg_t[hit_ft]))
    r_stop = entry + d * runner_lock
    if (first_target - r_stop) * d <= 0:                           # lock not past current price: use breakeven
        r_stop = entry
    rp, rt = seg_p[hit_ft:], seg_t[hit_ft:]
    r_hit_stop = _first((rp - r_stop) * d <= 0)
    r_hit_tgt = _first((rp - runner_target) * d >= 1)
    r_hit_end = _first(rt >= flatten_t)
    r_first = min(r_hit_stop, r_hit_tgt, r_hit_end)
    if r_first == len(rp):
        r_first, r_hit_end = len(rp) - 1, len(rp) - 1
    if r_first == r_hit_tgt and r_hit_tgt < min(r_hit_stop, r_hit_end):
        pts += (runner_target - entry) * d * runner
        reason = "first target + runner target"
    elif r_first == r_hit_stop:
        pts += (r_stop - d * slip - entry) * d * runner
        reason = "first target + runner stop"
    else:
        pts += (rp[r_first] - d * slip - entry) * d * runner
        reason = "first target + flatten"
    return TradeResult(reason, True, pts * tick_size, float(rt[r_first]))


# ---------------------------------------------------------------- setups

def _hhmm_to_ts(session: pd.Timestamp, hhmm: str) -> float:
    h, m = (int(x) for x in hhmm.split(":"))
    return pd.Timestamp(session.date()).replace(hour=h, minute=m).tz_localize(NY).timestamp()


def news_windows(path: Path, s: dict) -> dict:
    """Per session date: list of (no-entry start, no-entry end, flatten time) in epoch seconds.

    Events in [bot] no_trading_days (Fed decisions) block the whole session, 18:00 the evening
    before to 17:00."""
    bot = s["bot"]
    cal = pd.read_csv(path)
    out = {}
    for r in cal.itertuples():
        day = pd.Timestamp(r.date)
        if r.event in bot["no_trading_days"]:
            start = _hhmm_to_ts(day - pd.Timedelta(days=1), "18:00")
            out.setdefault(day, []).append((start, _hhmm_to_ts(day, "17:00"), start))
            continue
        rel = _hhmm_to_ts(day, r.time_et)
        out.setdefault(day, []).append((rel - bot["news_blackout_before_min"] * 60,
                                        max(_hhmm_to_ts(day, r.blackout_until_et),
                                            rel + bot["news_blackout_after_min"] * 60),
                                        rel - bot["close_before_news_min"] * 60))
    return out


@dataclass
class Session:
    date: pd.Timestamp
    p: np.ndarray            # trade path (adjusted ticks)
    t: np.ndarray
    tt: np.ndarray           # all trades: time, price (adjusted ticks), size, running CVD
    px: np.ndarray
    size: np.ndarray
    cvd: np.ndarray
    sells_t: np.ndarray      # trades by sell aggressors (at the bid): time, price
    sells_p: np.ndarray
    buys_t: np.ndarray       # trades by buy aggressors (at the ask)
    buys_p: np.ndarray
    bursts: pd.DataFrame     # 100+ bursts with direction, against_*s
    pocs: np.ndarray         # owner's recent uncovered POCs
    clusters: np.ndarray     # 2+ POC cluster centres (2-point grouping)
    zones: pd.DataFrame      # 15-minute S/D zones (owner's drawing) live at some point this session
    chops: pd.DataFrame      # 1-minute chop zones created this session (for the targets)
    m_start: np.ndarray      # 1-minute bars of this session: start time, low, high
    m_low: np.ndarray
    m_high: np.ndarray


def find_level(sess: Session, source: str, price: int, d: int, when: float, tol: int,
               front_only: bool = False):
    """The level (adjusted ticks) the stop is measured from, or None. Short = d -1.

    front_only: the burst must be at the level or in front of it (below it for
    a short, above it for a long), not past it.
    """
    def near(L):
        gap = (L - price) * -d            # short: L - price; >= 0 means in front of the level
        return (0 <= gap <= tol) if front_only else abs(price - L) <= tol

    cands = []
    if source in ("recent uncovered POC", "your levels (POC or zone)"):
        cands += [L for L in sess.pocs if near(L)]
    if source == "POC cluster":
        cands += [L for L in sess.clusters if near(L)]
    if source in ("S/D zone 15 min", "your levels (POC or zone)") and len(sess.zones):
        z = sess.zones
        kind = "supply" if d == -1 else "demand"
        far_edge = z["high"] if d == -1 else z["low"]
        inside = (z["low"] - tol <= price) & (price <= z["high"] + tol)
        if front_only:
            inside &= (far_edge - price) * -d >= 0
        ok = (z["kind"] == kind) & (z["created"] <= when) & (z["dies"] > when) & inside
        cands += list(z.loc[ok, "high" if d == -1 else "low"])     # far edge of the zone
    if source == "no level":
        cands = [price]
    if not cands:
        return None
    return min(cands, key=lambda L: abs(price - L))


def targets(sess: Session, d: int, entry: int, when: float):
    """First target and runner target from the last 1-minute chop before the move, or None."""
    kind = "demand" if d == -1 else "supply"           # a short's move came up out of a demand chop
    c = sess.chops[(sess.chops["kind"] == kind) & (sess.chops["created"] <= when)]
    edge = "high" if d == -1 else "low"
    c = c[(c[edge] - entry) * d > 0]                   # target must be on the profit side of the entry
    if c.empty:
        return None
    z = c.iloc[-1]                                     # most recent such chop
    ft = int(z[edge])
    done = (sess.m_start >= z["base_start"]) & (sess.m_start + 60 <= when)
    lows = list(sess.m_low[done]) if d == -1 else list(sess.m_high[done])
    a, b = np.searchsorted(sess.t, z["base_start"], "left"), np.searchsorted(sess.t, when, "right")
    if b > a:
        lows.append(sess.p[a:b].min() if d == -1 else sess.p[a:b].max())
    if not lows:
        return None
    rt = int(min(lows)) if d == -1 else int(max(lows))
    return ft, rt


def retest_trigger(sess: Session, d: int, L: int, t_first: float, s: dict, latest: float):
    """Retest entry (STRATEGY.md section 2): the time to enter, or None.

    After absorption at level L (the first test, around t_first), price pulls
    `pullaway` away, comes back within the tolerance of L without reaching the
    stop, and turns `trigger` ticks off its retest extreme. The retest must be
    weaker than the first test: less volume at the level, and CVD a lower high
    (short) or a higher low (long).
    """
    bt, bot = s["backtest"], s["bot"]
    tol = ticks(bt["level_tolerance_points"], s)
    pull = ticks(bt["retest_pullaway_points"], s)
    trig_k = bt["retest_trigger_ticks"]
    stop_buf = ticks(bot["stop_buffer_points"], s)
    wait = bt["retest_max_wait_minutes"] * 60
    tt, px, cvd = sess.tt, sess.px, sess.cvd
    i0 = np.searchsorted(tt, t_first - bt["lopsided_window_minutes"] * 60, "left")
    ib = np.searchsorted(tt, t_first, "left")
    away = (px[ib:] - L) * d >= pull                     # short: price at least `pull` below L
    if not away.any():
        return None
    il = ib + int(np.argmax(away))
    zone = np.abs(px - L) <= tol
    vol_first = sess.size[i0:il][zone[i0:il]].sum()
    cvd_first = cvd[i0:il].max() if d == -1 else cvd[i0:il].min()
    back = (px[il:] - L) * d <= tol                      # short: within tol below L (or above)
    if not back.any():
        return None
    ir = il + int(np.argmax(back))
    if tt[ir] - tt[il] > wait:
        return None
    iend = np.searchsorted(tt, latest, "right")
    seg = px[ir:iend]
    if len(seg) == 0:
        return None
    extreme = np.maximum.accumulate(seg) if d == -1 else np.minimum.accumulate(seg)
    trig = _first((extreme - seg) * -d >= trig_k)        # short: price trig_k below the retest high
    brk = _first((seg - (L - d * stop_buf)) * d <= 0)    # short: price reached the stop
    if trig == len(seg) or brk <= trig:
        return None
    it = ir + trig
    vol_retest = sess.size[ir:it + 1][zone[ir:it + 1]].sum()
    cvd_retest = cvd[ir:it + 1].max() if d == -1 else cvd[ir:it + 1].min()
    weaker = vol_retest < vol_first and ((cvd_retest < cvd_first) if d == -1 else (cvd_retest > cvd_first))
    return float(tt[it]) if weaker else None


def run_session(sess: Session, s: dict, v: dict, news: list) -> list[dict]:
    """The day's trades for one variant, as a list of dicts.

    v: a variant from settings [[backtest.variants]] (name, source, entry, start_et, end_et,
    front_only, min_contracts, min_bursts, check_seconds, check_ticks, slippage_ticks,
    exits, stop_points, target_points, max_trades).
    exits = "spec" (default): stop 3 points past the level, first target at the
    start of the move, runner. exits = "fixed" (journal style): stop and target a
    fixed distance from the entry, all contracts out at the target.
    A new trade can only start after the previous one has closed.
    """
    bt, bot = s["backtest"], s["bot"]
    tol = ticks(bt["level_tolerance_points"], s)
    stop_buf = ticks(bot["stop_buffer_points"], s)
    max_risk = ticks(bot["max_entry_distance_points"] + bot["stop_buffer_points"], s)
    start = _hhmm_to_ts(sess.date, v.get("start_et", "09:30"))
    cutoff = _hhmm_to_ts(sess.date, v.get("end_et", bt["entry_cutoff_et"]))
    flatten = _hhmm_to_ts(sess.date, bt["flatten_et"])
    flatten = min([flatten] + [f for _, _, f in news if f > _hhmm_to_ts(sess.date, "09:30")])
    latest = min(cutoff, flatten)
    window = bt["lopsided_window_minutes"] * 60
    y, x = v.get("check_seconds", 0), v.get("check_ticks", -1)
    slip = v["slippage_ticks"]
    b = sess.bursts
    fixed = v.get("exits", "spec") == "fixed"
    max_trades = v.get("max_trades", bot["max_trades_per_day"])
    trades, busy_until, day_usd = [], -np.inf, 0.0
    tried = {}                                           # retest mode: level -> time it was last tried
    for r in b.itertuples():
        if len(trades) >= max_trades or day_usd <= -bot["daily_loss_limit_usd"]:
            break
        if r.end_time <= busy_until or r.contracts < v.get("min_contracts", 100):
            continue
        d = int(r.direction)
        level = find_level(sess, v["source"], int(r.price), d, r.end_time, tol, v.get("front_only", False))
        if level is None:
            continue
        near = b[(b["end_time"] <= r.end_time) & (b["end_time"] > r.end_time - window)
                 & ((b["price"] - level).abs() <= tol)]
        mine = near.loc[near["direction"] == d, "contracts"].sum()
        theirs = near.loc[near["direction"] == -d, "contracts"].sum()
        if mine < theirs or (near["direction"] == d).sum() < v.get("min_bursts", 1):
            continue
        if v.get("entry", "early") == "retest":
            if (level, d) in tried and r.end_time - tried[(level, d)] < bt["retest_max_wait_minutes"] * 60:
                continue
            tried[(level, d)] = r.end_time
            when = retest_trigger(sess, d, level, r.end_time, s, latest)
            if when is None:
                continue
        else:
            when = r.end_time + y
            if y > 0 and getattr(r, f"against_{y}s") > x:
                continue
        if when < start or when > cutoff or when >= flatten or any(a <= when <= e for a, e, _ in news):
            continue
        ft_arr, fp_arr = (sess.sells_t, sess.sells_p) if d == -1 else (sess.buys_t, sess.buys_p)
        j = np.searchsorted(ft_arr, when, "left")      # short fills like the next trade at the bid
        if j >= len(ft_arr):
            continue
        fill_t = ft_arr[j]
        entry = int(fp_arr[j]) + d * slip              # slippage: short sells lower
        if fixed:
            stop = entry - d * ticks(v["stop_points"], s)
            ft = rt = entry + d * ticks(v["target_points"], s)
            risk = ticks(v["stop_points"], s)
            first_contracts = bot["contracts_per_trade"]
        else:
            stop = level - d * stop_buf
            risk = (stop - entry) * -d
            if risk <= 0 or risk > max_risk:
                continue
            tg = targets(sess, d, entry, fill_t)
            if tg is None:
                continue
            ft, rt = tg
            if (ft - entry) * d < bot["min_reward_to_risk"] * risk:
                continue
            first_contracts = bot["first_target_contracts"]
        i0 = int(np.searchsorted(sess.t, fill_t, "left"))
        res = simulate_trade(sess.p, sess.t, i0, entry, d, stop, ft, rt, flatten, slip,
                             contracts=bot["contracts_per_trade"], first_contracts=first_contracts,
                             runner_lock=ticks(bot["runner_stop_after_first_target_points"], s))
        cost = bt["commission_per_contract_round_trip"] * bot["contracts_per_trade"]
        usd = res.points * s["market"]["point_value_usd"] - cost
        busy_until = res.exit_time
        day_usd += usd
        tk = s["market"]["tick_size"]
        trades.append(dict(session=sess.date, variant=v["name"], source=v["source"], slippage_ticks=slip,
                    side="short" if d == -1 else "long", burst_time=r.end_time, burst_contracts=r.contracts,
                    entry_time=fill_t, level=level * tk, entry=entry * tk, stop=stop * tk,
                    first_target=ft * tk, runner_target=rt * tk, risk_points=risk * tk,
                    reward_points=(ft - entry) * d * tk, exit_reason=res.exit_reason,
                    first_target_hit=res.first_target_hit, points=res.points, usd=usd,
                    exit_time=res.exit_time))
    return trades


# ---------------------------------------------------------------- whole backtest

def build_sessions(s: dict):
    """Everything each trading day needs, built once."""
    bot = s["bot"]
    fb, sessions, _ = prepare(s)
    files = session_files()
    days = [d for d, _, _ in files]
    fb_bt = fb[fb["session"].isin(days)]
    m1 = make_candles(fb_bt, 1)
    chops = find_zones(m1, bot["sd_base_min_candles"], bot["sd_departure_min_ratio"], "whole chop wicks")
    zones = pd.read_csv(DERIVED / "sd_zones_15min_last_chop_candle_body.csv")
    events = pd.read_parquet(DERIVED / "absorption_check_events.parquet")
    usable = list(sessions.index[sessions["use"]])
    out = []
    for day, contract, f in files:
        if day not in sessions.index or not sessions.loc[day, "use"] or sessions.loc[day, "contract"] != contract:
            continue
        off = int(sessions.loc[day, "offset_ticks"])
        tr = load_trades(f, s)
        tt = epoch_seconds(tr["ts"])
        px = tr["price"].to_numpy().astype(np.int64) + off
        side = tr["side"].to_numpy()
        size = tr["size"].to_numpy().astype(np.int64)
        cvd = np.cumsum(np.where(side == "B", size, np.where(side == "A", -size, 0)))
        p, t = trade_path(px, tt)
        k = usable.index(day)
        prior = sessions.loc[usable[k - bot["poc_lookback_sessions"]:k], "poc_adj"].to_numpy()
        rec = recent_poc_labels(sessions, pd.Index([day]), bot["recent_poc_lookback_sessions"])
        mb = m1[m1["session"] == day]
        out.append(Session(
            date=day, p=p, t=t, tt=tt, px=px, size=size, cvd=cvd,
            sells_t=tt[side == "A"], sells_p=px[side == "A"], buys_t=tt[side == "B"], buys_p=px[side == "B"],
            bursts=events[events["session"] == day].sort_values("end_time").reset_index(drop=True),
            pocs=rec.loc[~rec["covered"], "price"].to_numpy(),
            clusters=np.array([g.price for g in cluster_pocs(prior, 8) if g.count >= bot["pocs_for_level"]]),
            zones=zones[(zones["created"] < t[-1]) & (zones["dies"] > t[0])].reset_index(drop=True),
            chops=chops[chops["created_session"] == day].sort_values("created").reset_index(drop=True),
            m_start=mb["start"].to_numpy(), m_low=mb["low"].to_numpy(), m_high=mb["high"].to_numpy()))
    return out


def summarise(trades: pd.DataFrame, s: dict) -> pd.DataFrame:
    rng = np.random.default_rng(s["level_study"]["random_seed"])
    rows = []
    keys = ["variant", "source", "slippage_ticks"]
    for k, g in trades.groupby(keys, sort=False):
        usd = g["usd"].to_numpy()
        wins, losses = usd[usd > 0].sum(), -usd[usd < 0].sum()
        eq = np.cumsum(usd)
        dd = (np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()
        boot = usd[rng.integers(0, len(usd), size=(2000, len(usd)))].mean(axis=1)
        rows.append(dict(zip(keys, k), trades=len(g), first_target_rate=g["first_target_hit"].mean(),
                         stopped=(g["exit_reason"] == "stop").mean(), avg_usd=usd.mean(),
                         avg_usd_low=np.percentile(boot, 2.5), avg_usd_high=np.percentile(boot, 97.5),
                         total_usd=usd.sum(), profit_factor=wins / losses if losses else np.inf,
                         max_drawdown_usd=dd, avg_risk_points=g["risk_points"].mean(),
                         avg_reward_points=g["reward_points"].mean()))
    return pd.DataFrame(rows)


def main() -> None:
    s = load_settings()
    bt = s["backtest"]
    t0 = time.time()
    days = build_sessions(s)
    news = news_windows(REPO / bt["news_calendar"], s)
    print(f"{len(days)} sessions ready in {time.time() - t0:.0f}s")
    variants = [dict(v, source=src, slippage_ticks=slip)
                for v in bt["variants"] for src in v["sources"] for slip in v.get("slippages", [1])]
    rows = []
    for sess in days:
        for v in variants:
            rows += run_session(sess, s, v, news.get(sess.date, []))
    trades = pd.DataFrame(rows)
    STEP2_OUT.mkdir(parents=True, exist_ok=True)
    trades.to_csv(STEP2_OUT / "backtest_trades.csv", index=False)
    summary = summarise(trades, s)
    summary.to_csv(STEP2_OUT / "backtest_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(summary.round(2).to_string(index=False))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
