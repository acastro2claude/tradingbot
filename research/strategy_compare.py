"""Other order-flow strategies against the owner's setup (owner's request, Nov 2026).

Every strategy uses the owner's risk and filters ([strategy_compare] lists the strategy numbers):
5 MES, stop-market 4.5 points, target 10, stop to +1 at +7, one trade at a time, -$500 day stop;
entries 10:00-15:30 ET outside news-spike windows and not on Fed days (research.entry_read.filters),
except intraday momentum, which trades the last half hour by design. Baselines: random entries and
the owner's own picks on the 2026 replay clips.

  5  intraday momentum (Gao, Han, Li, Zhou, JFE 2018): sign of the return from the prior 16:00
     close to 10:00 -> enter 15:30 that way; exit at 15:59 with the 4.5 stop, or the owner's bracket.
     1-minute bars: ES Jan 2024 - Feb 2025, MES Mar 2025 - Oct 2026.
  4  liquidity sweep and reclaim at the prior regular-session high/low and the overnight high/low:
     price trades 1+ tick past the level but at most 3 points, then back inside by 2 ticks within
     5 minutes -> fade. Bar version on all bars; tick version on MES 2026 trades also needs the
     aggressive volume our way to be at least the other side's over the last 30 s.
  2  order-flow momentum: the 5-minute aggressive volume imbalance crosses the 90th percentile of
     |imbalance| (set on the first 20 sessions of 2026) -> follow it (and, separately, fade it).
     MES 2026 trades; results on the sessions after the first 20.
  1  book imbalance at the touch (python -m research.strategy_compare --book): see book_imbalance().

Writes research/results/journal/strategy_compare_trades.csv and strategy_compare_summary.csv.
Run:   python -m research.strategy_compare
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.absorption_study import session_files
from research.bot_week_study import SLIP_TICKS, et
from research.entry_read import E as ER
from research.entry_read import filters, news_blocks
from research.exits import bot_exit
from research.journal_study import OUT
from research.market_data import epoch_seconds, load_trades
from research.owner_rules import DayTrades
from research.settings import load_settings

S = load_settings()
C = S["strategy_compare"]
B = S["bot"]
TICK = S["market"]["tick_size"]
CONTRACTS = B["contracts_per_trade"]
RNG = np.random.default_rng(S["level_study"]["random_seed"])


def usd(points):
    return points * S["market"]["point_value_usd"] * CONTRACTS - S["backtest"]["commission_per_contract_round_trip"] * CONTRACTS


def tk(points: float) -> int:
    return int(round(points / TICK))


# ---------- 1-minute bars ----------

def all_bars() -> pd.DataFrame:
    from research.level_study import prepare
    from research.zone_course_chart import es_zones
    es, _, _, _ = es_zones(S)
    mes, _, _ = prepare(S)
    cut = pd.Timestamp(C["es_until"])
    es = es[pd.to_datetime(es["session"]) < cut].assign(market="ES")
    mes = mes[pd.to_datetime(mes["session"]) >= cut].assign(market="MES")
    b = pd.concat([es, mes], ignore_index=True).sort_values("ts").reset_index(drop=True)
    loc = b["ts"].dt.tz_convert("America/New_York")
    b["mins"] = loc.dt.hour * 60 + loc.dt.minute
    b["hm"] = loc.dt.strftime("%H:%M")
    b["date"] = loc.dt.date.astype(str)
    b["session"] = pd.to_datetime(b["session"]).dt.date.astype(str)
    return b


def bar_bracket(lo, hi, cl, k0: int, end: int, fill: int, d: int, stop_pts: float, tgt_pts: float | None,
                move: bool = True) -> tuple[float, int]:
    """Ticks per contract on 1-minute bars from the bar after k0 to `end` (its close). A bar that reaches
    the stop counts as stopped first. The stop moves to +1 once a bar reaches +7 (from the next bar)."""
    stop = fill - d * tk(stop_pts)
    tgt = fill + d * tk(tgt_pts) if tgt_pts else None
    moved = False
    for k in range(k0 + 1, end + 1):
        if (lo[k] <= stop) if d == 1 else (hi[k] >= stop):
            return float((stop - fill) * d - SLIP_TICKS), k
        if tgt is not None and ((hi[k] >= tgt + 1) if d == 1 else (lo[k] <= tgt - 1)):
            return float(tk(tgt_pts)), k
        if move and not moved and ((hi[k] - fill) * d >= tk(B["breakeven_trigger_points"]) if d == 1
                                   else (fill - lo[k]) >= tk(B["breakeven_trigger_points"])):
            stop, moved = fill + d * tk(B["breakeven_stop_points"]), True
    return float((cl[end] - fill) * d - SLIP_TICKS), end


def intraday_momentum(b: pd.DataFrame) -> pd.DataFrame:
    rows = []
    lo, hi, cl = (b[c].to_numpy() for c in ("low", "high", "close"))
    cla = b["close_adj"].to_numpy()
    prev_close = None
    for ses, g in b.groupby("session", sort=True):
        g = g[g["date"] == ses]                                   # the regular hours of the session's own date
        idx = {h: i for h, i in zip(g["hm"], g.index)}
        sig, ent, ex = (idx.get(_minus1(C[k])) for k in ("momentum_signal_et", "momentum_entry_et", "momentum_exit_et"))
        close16 = idx.get("15:59")
        if prev_close is not None and None not in (sig, ent, ex) and ses not in ER["fed_days"]:
            r1 = cla[sig] - prev_close
            if r1 != 0:
                d = 1 if r1 > 0 else -1
                fill = int(cl[ent]) + d * SLIP_TICKS
                rd = int(RNG.choice([-1, 1]))
                for name, tgt in (("hold to 15:59, 4.5 stop", None), ("owner's bracket", B["target_points"])):
                    for strat, dd in (("5 intraday momentum", d), ("random direction at 15:30", rd)):
                        f_ = int(cl[ent]) + dd * SLIP_TICKS
                        pts, _ = bar_bracket(lo, hi, cl, ent, ex, f_, dd, B["stop_points"], tgt, move=tgt is not None)
                        rows.append(dict(strategy=strat, variant=name, day=ses, market=g["market"].iat[0],
                                         signal_points=r1 * TICK, points=pts * TICK))
        prev_close = cla[close16] if close16 is not None else None
    return pd.DataFrame(rows)


def _minus1(hhmm: str) -> str:
    """The bar that closes at hh:mm starts a minute earlier."""
    h, m = map(int, hhmm.split(":"))
    t = h * 60 + m - (0 if hhmm == "15:59" else 1)
    return f"{t // 60:02d}:{t % 60:02d}"


def random_bars(b: pd.DataFrame, per_day: int = 20) -> pd.DataFrame:
    """Random entries on bars (random minute 10:00-15:30, random side), the owner's bracket: the no-edge baseline."""
    rows = []
    lo, hi, cl = (b[c].to_numpy() for c in ("low", "high", "close"))
    for ses, g in b.groupby("session", sort=True):
        rth = g[(g["date"] == ses) & (g["mins"] >= 600) & (g["mins"] < 930)]
        last = g[(g["date"] == ses) & (g["mins"] <= 958)]
        if not len(rth) or not len(last) or ses in ER["fed_days"]:
            continue
        for k in RNG.choice(rth.index.to_numpy(), per_day):
            d = int(RNG.choice([-1, 1]))
            pts, _ = bar_bracket(lo, hi, cl, k, last.index[-1], int(cl[k]) + d * SLIP_TICKS, d, B["stop_points"], B["target_points"])
            rows.append(dict(strategy="random entries", variant="bars, 10:00-15:30", day=ses, market=g["market"].iat[0],
                             points=pts * TICK))
    return pd.DataFrame(rows)


def levels_for(b: pd.DataFrame) -> dict:
    """Per session: prior regular-session high/low and overnight high/low (raw ticks of the session's contract)."""
    out, prev = {}, None
    for ses, g in b.groupby("session", sort=True):
        rth = g[(g["date"] == ses) & (g["mins"] >= 570) & (g["mins"] < 960)]
        night = g[~g.index.isin(rth.index) & ((g["date"] < ses) | (g["mins"] < 570))]
        lv = []
        if len(night):
            lv += [("overnight high", int(night["high"].max()), -1), ("overnight low", int(night["low"].min()), 1)]
        if prev is not None and prev[0] == g["contract"].iat[0]:
            lv += [("prior day high", prev[1], -1), ("prior day low", prev[2], 1)]
        out[ses] = lv
        if len(rth):
            prev = (g["contract"].iat[0], int(rth["high"].max()), int(rth["low"].min()))
    return out


def sweeps_bars(b: pd.DataFrame, minute_vol) -> pd.DataFrame:
    rows = []
    lo, hi, cl = (b[c].to_numpy() for c in ("low", "high", "close"))
    lv = levels_for(b)
    a0, a1 = 600, 930
    for ses, g in b.groupby("session", sort=True):
        if ses in ER["fed_days"]:
            continue
        rth = g[(g["date"] == ses) & (g["mins"] >= 570) & (g["mins"] < 960)]
        if not len(rth):
            continue
        end = rth.index[rth["mins"] <= 958][-1] if (rth["mins"] <= 958).any() else rth.index[-1]
        mv = minute_vol.get(ses, pd.Series(dtype=float))
        blocks = news_blocks(mv)
        trades, busy, pnl = [], -1, 0.0
        mins = rth["mins"].to_numpy()
        for name, L, d in lv.get(ses, []):            # d: +1 long at a low, -1 short at a high
            poke, inside_before = None, False
            for k, m in zip(rth.index, mins):
                past = (L - lo[k]) if d == 1 else (hi[k] - L)
                if poke is None:
                    fresh = past >= C["sweep_min_ticks"] and inside_before and a0 <= m < a1
                    inside_before = past < C["sweep_min_ticks"]
                    if not fresh:
                        continue
                    poke, worst = k, past
                worst = max(worst, past)
                if worst > tk(C["sweep_max_points"]) or k - poke > C["reclaim_minutes"]:
                    break                               # a breakout, or no reclaim in time: this level is done
                if (cl[k] - L) * d >= C["reclaim_ticks"]:
                    if not any(x <= m < y for x, y in blocks):
                        trades.append((k, name, d))
                    break
        for k, name, d in sorted(trades):
            if k <= busy or pnl <= -B["daily_loss_limit_usd"]:
                continue
            fill = int(cl[k]) + d * SLIP_TICKS
            pts, k_out = bar_bracket(lo, hi, cl, k, end, fill, d, B["stop_points"], B["target_points"])
            rows.append(dict(strategy="4 sweep and reclaim", variant=f"bars, {name.split()[0]} levels", day=ses,
                             market=g["market"].iat[0], level=name, points=pts * TICK))
            busy, pnl = k_out, pnl + usd(pts * TICK)
    return pd.DataFrame(rows)


# ---------- MES 2026 ticks ----------

BAR_CONTRACT: dict = {}


def tick_strategies(b: pd.DataFrame, minute_vol) -> pd.DataFrame:
    lv = levels_for(b)
    BAR_CONTRACT.update(b.groupby("session")["contract"].first().to_dict())
    files = [(d.date().isoformat(), c, f) for d, c, f in session_files()]
    imb_build, cands = [], []
    win = C["flow_window_minutes"] * 60
    for n, (day, contract, f) in enumerate(files):
        tr = load_trades(f, S)
        tr = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                               size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        dt = DayTrades(trades=tr)
        mv = minute_vol.get(day, pd.Series(dtype=float))
        flat = et(day, "15:59")
        cb = np.concatenate([[0], np.cumsum(dt.buy)])
        cs = np.concatenate([[0], np.cumsum(dt.sell)])
        # 2: the 5-minute imbalance each second from 10:00 to 15:30
        secs = np.arange(et(day, "10:00"), et(day, "15:30"))
        j1, j0 = np.searchsorted(dt.t, secs), np.searchsorted(dt.t, secs - win)
        bb, ss = cb[j1] - cb[j0], cs[j1] - cs[j0]
        imb = np.where(bb + ss > 0, (bb - ss) / np.maximum(bb + ss, 1), 0.0)
        if n < C["flow_build_sessions"]:
            imb_build.append(np.abs(imb[::60]))
        cands.append(("flow", day, dt, secs, imb, mv, flat))
        # 4 ticks: sweep and reclaim with the flow flip (levels from the same contract's bars)
        rows = []
        same = BAR_CONTRACT.get(day) == contract
        for name, L, d in (lv.get(day, []) if same else []):
            k0 = int(np.searchsorted(dt.t, et(day, "10:00")))
            k1 = int(np.searchsorted(dt.t, et(day, "15:30")))
            px = dt.px[k0:k1]
            past = (L - px) if d == 1 else (px - L)
            hit = np.flatnonzero((past[1:] >= C["sweep_min_ticks"]) & (past[:-1] < C["sweep_min_ticks"])) + 1
            if not len(hit):
                continue
            a = k0 + hit[0]                                       # the first fresh poke past the level
            t_poke = dt.t[a]
            b_ = int(np.searchsorted(dt.t, t_poke + C["reclaim_minutes"] * 60))
            seg = (L - dt.px[a:b_]) if d == 1 else (dt.px[a:b_] - L)
            over = np.flatnonzero(seg > tk(C["sweep_max_points"]))
            stop_at = a + (over[0] if len(over) else len(seg))
            for k in range(a, stop_at):
                if (dt.px[k] - L) * d < C["reclaim_ticks"]:
                    continue
                t = dt.t[k]
                i0 = int(np.searchsorted(dt.t, t - C["flip_seconds"]))
                ours = (cb[k + 1] - cb[i0]) if d == 1 else (cs[k + 1] - cs[i0])
                theirs = (cs[k + 1] - cs[i0]) if d == 1 else (cb[k + 1] - cb[i0])
                if ours >= theirs > 0 or (ours > 0 and theirs == 0):
                    if filters(day, t, mv, 0.0, 0.0) is None:
                        rows.append((float(t), int(dt.px[k]), d, name))
                    break
        cands.append(("sweep", day, dt, rows, None, mv, flat))
        print(day, flush=True)
    thr = float(np.percentile(np.concatenate(imb_build), C["flow_percentile"]))
    build_days = {files[i][0] for i in range(C["flow_build_sessions"])}
    out = []
    for kind, day, dt, a, b2, mv, flat in cands:
        entries = []
        if kind == "flow":
            secs, imb = a, b2
            up = np.flatnonzero((np.abs(imb[1:]) >= thr) & (np.abs(imb[:-1]) < thr)) + 1
            for k in up:
                t = float(secs[k])
                if filters(day, t, mv, 0.0, 0.0) is not None:
                    continue
                i = int(np.searchsorted(dt.t, t, "right")) - 1
                d = 1 if imb[k] > 0 else -1
                entries += [("2 order-flow momentum", "follow", t, int(dt.px[i]), d),
                            ("2 order-flow momentum", "fade", t, int(dt.px[i]), -d)]
        else:
            entries = [("4 sweep and reclaim", f"ticks + flow flip, {name.split()[0]} levels", t, p, d) for t, p, d, name in a]
        for (strat, var), g in pd.DataFrame(entries, columns=["s", "v", "t", "p", "d"]).groupby(["s", "v"]) if entries else []:
            busy, pnl = -np.inf, 0.0
            for r in g.sort_values("t").itertuples():
                if r.t < busy or pnl <= -B["daily_loss_limit_usd"]:
                    continue
                pts, t_out, _ = bot_exit(dt, r.t, r.p + r.d * SLIP_TICKS, r.d, flat)
                out.append(dict(strategy=strat, variant=var, day=day, market="MES", points=pts * TICK,
                                build=day in build_days if strat.startswith("2") else False))
                busy, pnl = t_out, pnl + usd(pts * TICK)
    print("order-flow threshold |imbalance| >=", round(thr, 3))
    return pd.DataFrame(out)


def summarize(x: pd.DataFrame) -> pd.DataFrame:
    x = x[~x.get("build", False).fillna(False).astype(bool)] if "build" in x else x
    rows = []
    for (s_, v), g in x.groupby(["strategy", "variant"]):
        u = usd(g["points"])
        yr = g.groupby(pd.to_datetime(g["day"]).dt.year).apply(lambda h: usd(h["points"]).mean())
        rows.append(dict(strategy=s_, variant=v, trades=len(g), days=g["day"].nunique(),
                         win_rate=(g["points"] > 0).mean(), usd_per_trade=u.mean(), net_usd=u.sum(),
                         by_year=" ".join(f"{y}: {v_:+.0f}" for y, v_ in yr.items()),
                         ci95=1.96 * u.std() / np.sqrt(len(u)) if len(u) > 1 else np.nan))
    return pd.DataFrame(rows)


def book_imbalance() -> None:
    """1: queue imbalance at the touch on the 16 MES 2026 days with the full book.

    Each second 10:00-15:30 ET: I = (best bid size - best ask size) / (sum), from the book at the end of
    that second. Then (a) the average mid-price move over the next 1, 5 and 30 s by imbalance bucket, in
    ticks, and (b) a rule: I >= 0.8 -> buy at the ask, sell at the bid h seconds later (I <= -0.8: the
    mirror), one trade at a time, in ticks per contract before commission ($1.24 a round trip is about
    1 tick). Fills at the book a full second old are optimistic; a real bot is slower than that.
    Writes research/results/journal/strategy_compare_book.csv.
    """
    from research.orderbook import build_book, load_mbo
    from research.replay_export import days_2026
    hs, thr = C["book_horizons_seconds"], C["book_rule_threshold"]
    rows, trades = [], []
    for d in days_2026():
        book = build_book(load_mbo(d["file"]), int(et(d["day"], "09:30")), int(et(d["day"], "16:00")))
        r0, r1 = book.row(et(d["day"], "10:00")), book.row(et(d["day"], "15:30"))
        bidp, askp, qb, qa = [], [], [], []
        for r in range(r0, r1 + max(hs) + 1):
            bb, ba = book.best(r)
            bidp.append(bb)
            askp.append(ba)
            qb.append(book.bid[r, bb - book.lo] if bb >= 0 else 0)
            qa.append(book.ask[r, ba - book.lo] if ba >= 0 else 0)
        bidp, askp, qb, qa = (np.array(v, dtype=float) for v in (bidp, askp, qb, qa))
        ok = (bidp > 0) & (askp > bidp)
        imb = np.where(ok, (qb - qa) / np.maximum(qb + qa, 1), np.nan)
        mid = (bidp + askp) / 2
        n = r1 - r0 + 1
        for h in hs:
            dm = mid[h:h + n] - mid[:n]
            rows.append(pd.DataFrame(dict(day=d["day"], h=h, imb=imb[:n], dmid=dm, ok=ok[:n] & ok[h:h + n])))
            busy = -1
            for k in np.flatnonzero(np.abs(np.nan_to_num(imb[:n])) >= thr):
                if k < busy or not (ok[k] and ok[k + h]):
                    continue
                sd = 1 if imb[k] > 0 else -1
                ticks = (bidp[k + h] - askp[k]) if sd == 1 else (bidp[k] - askp[k + h])
                trades.append(dict(day=d["day"], h=h, ticks=ticks))
                busy = k + h
        print(d["day"], flush=True)
        del book
    x = pd.concat(rows, ignore_index=True)
    x = x[x["ok"]]
    x["bucket"] = pd.cut(x["imb"], [-1.01, -0.8, -0.4, 0, 0.4, 0.8, 1.01])
    tab = x.groupby(["h", "bucket"], observed=True)["dmid"].agg(["mean", "size"]).round(3)
    tr = pd.DataFrame(trades)
    summ = tr.groupby("h")["ticks"].agg(trades="size", gross_ticks_per_trade="mean").round(3)
    summ["net_ticks_after_commission"] = summ["gross_ticks_per_trade"] - S["backtest"]["commission_per_contract_round_trip"] / (S["market"]["point_value_usd"] * TICK)
    tab.reset_index().to_csv(OUT / "strategy_compare_book.csv", index=False)
    summ.to_csv(OUT / "strategy_compare_book_rule.csv")
    pd.set_option("display.width", 200)
    print(tab.to_string())
    print(summ.to_string())


def main() -> None:
    b = all_bars()
    minute_vol = {d: g.droplevel(0) for d, g in b.groupby(["date", "hm"])["volume"].sum().groupby(level=0)}
    parts = [intraday_momentum(b), sweeps_bars(b, minute_vol), random_bars(b), tick_strategies(b, minute_vol)]
    x = pd.concat(parts, ignore_index=True)
    x.to_csv(OUT / "strategy_compare_trades.csv", index=False)
    t = summarize(x)
    t.to_csv(OUT / "strategy_compare_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(t.round(2).to_string())


if __name__ == "__main__":
    import sys
    book_imbalance() if "--book" in sys.argv else main()
