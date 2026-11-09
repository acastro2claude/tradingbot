"""Each strategy as it is usually traded, before and after costs (owner, Nov 2026: "run all of them
just for comparison ... one of these should be a decent strategy").

research.strategy_compare put every strategy on the owner's setup (4.5 stop, 10 target, filters).
Here each one gets its own usual exits and no owner filters, and every result is shown twice:
  gross = entry at the signal price, exits without slippage, no commission (the strategy's edge)
  net   = 1 tick slippage on the entry and on stop / market exits, $1.24 a contract commission
Baseline for each: the same moments with a random side and the same exits (an edge has to beat it).
5 MES ($25 a point).

  5  intraday momentum as published: 15:30 -> 16:00 in the direction of the prior close -> 10:00
     return, no stop, every day (1-minute bars 2024-2026)
  4  sweep and reclaim, any time 09:35-15:55: stop 1 tick beyond the sweep's extreme, target twice
     that risk (bars 2024-2026; ticks 2026 with the flow flip)
  2  order-flow momentum, 09:35-15:55: hold 1 or 5 minutes, then out at market (ticks 2026, after
     the 20 sessions that set the threshold)
  3  iceberg / refill (16 MES days of 2026 with the full book, `--iceberg`): at the best bid, in 30 s
     more contracts filled than 2x the most ever showing there and 2x the day's typical size, while
     the bid held at that price or better -> buy (mirror at the ask). Stop 2 ticks through the
     iceberg price, target twice the risk; also with the owner's bracket.
  1  book imbalance: research.strategy_compare --book (already before and after costs).

Writes research/results/journal/strategy_natural.csv (and strategy_natural_iceberg.csv).
Run:   python -m research.strategy_natural          python -m research.strategy_natural --iceberg
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.absorption_study import session_files
from research.bot_week_study import SLIP_TICKS, et
from research.exits import bracket_exit
from research.journal_study import OUT
from research.market_data import epoch_seconds, load_trades
from research.owner_rules import DayTrades
from research.strategy_compare import BAR_CONTRACT, C, RNG, S, TICK, all_bars, bar_bracket, levels_for, tk

COMM = S["backtest"]["commission_per_contract_round_trip"] * S["bot"]["contracts_per_trade"]
PT_USD = S["market"]["point_value_usd"] * S["bot"]["contracts_per_trade"]


def row(strategy, variant, day, gross_ticks, net_ticks, side):
    return dict(strategy=strategy, variant=variant, day=day, side=side, gross_points=gross_ticks * TICK,
                net_usd=net_ticks * TICK * PT_USD - COMM)


def momentum(b) -> list:
    out, prev = [], None
    cl, cla = b["close"].to_numpy(), b["close_adj"].to_numpy()
    for ses, g in b.groupby("session", sort=True):
        g = g[g["date"] == ses]
        idx = dict(zip(g["hm"], g.index))
        if prev is not None and all(k in idx for k in ("09:59", "15:29", "15:59")):
            r1 = cla[idx["09:59"]] - prev
            if r1:
                d = 1 if r1 > 0 else -1
                for name, dd in (("as published", d), ("random side", int(RNG.choice([-1, 1])))):
                    gr = (cl[idx["15:59"]] - cl[idx["15:29"]]) * dd
                    out.append(row("5 intraday momentum", name, ses, gr, gr - 2 * SLIP_TICKS, name))
        prev = cla[idx["15:59"]] if "15:59" in idx else None
    return out


def sweep_signals_bars(b) -> list:
    """(session, bar index, side, risk ticks) for every fresh sweep and reclaim, 09:35-15:55."""
    lo, hi, cl = (b[c].to_numpy() for c in ("low", "high", "close"))
    lv, sig = levels_for(b), []
    for ses, g in b.groupby("session", sort=True):
        rth = g[(g["date"] == ses) & (g["mins"] >= 570) & (g["mins"] < 960)]
        for name, L, d in lv.get(ses, []):
            poke, inside = None, False
            for k, m in zip(rth.index, rth["mins"].to_numpy()):
                past = (L - lo[k]) if d == 1 else (hi[k] - L)
                if poke is None:
                    fresh = past >= C["sweep_min_ticks"] and inside and 575 <= m < 955
                    inside = past < C["sweep_min_ticks"]
                    if not fresh:
                        continue
                    poke, ext = k, (lo[k] if d == 1 else hi[k])
                ext = min(ext, lo[k]) if d == 1 else max(ext, hi[k])
                if (L - ext) * d > tk(C["sweep_max_points"]) or k - poke > C["reclaim_minutes"]:
                    break
                if (cl[k] - L) * d >= C["reclaim_ticks"]:
                    risk = (cl[k] - (ext - d)) * d
                    end = rth.index[rth["mins"] <= 958][-1]
                    sig.append((ses, k, d, int(risk), end, name))
                    break
    return sig


def sweeps_bars(b) -> list:
    lo, hi, cl = (b[c].to_numpy() for c in ("low", "high", "close"))
    out = []
    for ses, k, d, risk, end, name in sweep_signals_bars(b):
        for side_name, dd in (("as usually traded", d), ("random side", int(RNG.choice([-1, 1])))):
            gr, _ = bar_bracket(lo, hi, cl, k, end, int(cl[k]), dd, risk * TICK, 2 * risk * TICK, move=False, slip=0)
            nt, _ = bar_bracket(lo, hi, cl, k, end, int(cl[k]) + dd * SLIP_TICKS, dd, risk * TICK, 2 * risk * TICK,
                                move=False)
            out.append(row("4 sweep and reclaim", f"bars, {name.split()[0]} levels", ses, gr, nt, side_name))
    return out


def px_at(dt, t) -> int:
    i = int(np.searchsorted(dt.t, t, "right")) - 1
    return int(dt.px[max(i, 0)])


def ticks_part(b) -> list:
    lv = levels_for(b)
    BAR_CONTRACT.update(b.groupby("session")["contract"].first().to_dict())
    files = [(d.date().isoformat(), c, f) for d, c, f in session_files()]
    win, out, build, days = C["flow_window_minutes"] * 60, [], [], []
    for n, (day, contract, f) in enumerate(files):
        tr = load_trades(f, S)
        tr = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                               size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        dt = DayTrades(trades=tr)
        cb, cs = np.concatenate([[0], np.cumsum(dt.buy)]), np.concatenate([[0], np.cumsum(dt.sell)])
        secs = np.arange(et(day, "09:35"), et(day, "15:55"))
        j1, j0 = np.searchsorted(dt.t, secs), np.searchsorted(dt.t, secs - win)
        bb, ss = cb[j1] - cb[j0], cs[j1] - cs[j0]
        imb = np.where(bb + ss > 0, (bb - ss) / np.maximum(bb + ss, 1), 0.0)
        if n < C["flow_build_sessions"]:
            build.append(np.abs(imb[::60]))
        days.append((day, contract, dt, secs, imb, cb, cs))
        print(day, flush=True)
    thr = float(np.percentile(np.concatenate(build), C["flow_percentile"]))
    for n, (day, contract, dt, secs, imb, cb, cs) in enumerate(days):
        flat = et(day, "15:59")
        if n >= C["flow_build_sessions"]:                   # 2: order-flow momentum, results after the build sessions
            up = np.flatnonzero((np.abs(imb[1:]) >= thr) & (np.abs(imb[:-1]) < thr)) + 1
            for hold in (60, 300):
                busy = -np.inf
                for k in up:
                    t = float(secs[k])
                    if t < busy:
                        continue
                    d = 1 if imb[k] > 0 else -1
                    p0, p1 = px_at(dt, t), px_at(dt, min(t + hold, flat))
                    for side_name, dd in (("as usually traded", d), ("random side", int(RNG.choice([-1, 1])))):
                        gr = (p1 - p0) * dd
                        out.append(row("2 order-flow momentum", f"follow, hold {hold // 60} min", day, gr, gr - 2 * SLIP_TICKS, side_name))
                    busy = t + hold
        if BAR_CONTRACT.get(day) != contract:                # 4 ticks: levels from the same contract
            continue
        for name, L, d in lv.get(day, []):
            k0, k1 = int(np.searchsorted(dt.t, et(day, "09:35"))), int(np.searchsorted(dt.t, et(day, "15:55")))
            px = dt.px[k0:k1]
            past = (L - px) if d == 1 else (px - L)
            hit = np.flatnonzero((past[1:] >= C["sweep_min_ticks"]) & (past[:-1] < C["sweep_min_ticks"])) + 1
            if not len(hit):
                continue
            a = k0 + hit[0]
            e = int(np.searchsorted(dt.t, dt.t[a] + C["reclaim_minutes"] * 60))
            ext = dt.px[a]
            for k in range(a, e):
                ext = min(ext, dt.px[k]) if d == 1 else max(ext, dt.px[k])
                if (L - ext) * d > tk(C["sweep_max_points"]):
                    break
                if (dt.px[k] - L) * d < C["reclaim_ticks"]:
                    continue
                i0 = int(np.searchsorted(dt.t, dt.t[k] - C["flip_seconds"]))
                ours = (cb[k + 1] - cb[i0]) if d == 1 else (cs[k + 1] - cs[i0])
                theirs = (cs[k + 1] - cs[i0]) if d == 1 else (cb[k + 1] - cb[i0])
                if ours > 0 and ours >= theirs:
                    risk = int((dt.px[k] - (ext - d)) * d)
                    for side_name, dd in (("as usually traded", d), ("random side", int(RNG.choice([-1, 1])))):
                        gr = bracket_exit(dt, float(dt.t[k]), int(dt.px[k]), dd, flat, risk * TICK, 2 * risk * TICK, slip=0)[0]
                        nt = bracket_exit(dt, float(dt.t[k]), int(dt.px[k]) + dd * SLIP_TICKS, dd, flat, risk * TICK,
                                          2 * risk * TICK)[0]
                        out.append(row("4 sweep and reclaim", f"ticks + flow flip, {name.split()[0]} levels", day, gr, nt, side_name))
                    break
    print("order-flow threshold", round(thr, 3))
    return out


def iceberg() -> None:
    from research.orderbook import ASK, BID, build_book, load_mbo
    from research.orderbook_study import typical_depth
    from research.replay_export import days_2026
    W, out = 30, []
    for dd_ in days_2026():
        day = dd_["day"]
        book = build_book(load_mbo(dd_["file"]), int(et(day, "09:30")), int(et(day, "16:00")))
        typ = typical_depth(book)
        dt = DayTrades(trades=book.trades)
        flat = et(day, "15:59")
        n, ncol = book.bid.shape
        f = book.flows
        grids = {}
        for side in (BID, ASK):
            g = np.zeros((n + 1, ncol))
            ff = f[(f["side"] == side) & (f["sec"] >= 0) & (f["sec"] < n)]
            np.add.at(g, (ff["sec"].to_numpy() + 1, np.clip(ff["price"].to_numpy() - book.lo, 0, ncol - 1)), ff["filled"].to_numpy())
            grids[side] = np.cumsum(g, axis=0)                 # filled up to and including row r: grids[side][r + 1]
        nzb, nza = book.bid > 0, book.ask > 0
        bbi = np.where(nzb.any(1), ncol - 1 - np.argmax(nzb[:, ::-1], axis=1), -1)
        bai = np.where(nza.any(1), np.argmax(nza, axis=1), -1)
        r_from, r_to = book.row(et(day, "09:35")), book.row(et(day, "15:55"))
        last_sig = {}
        for r in range(max(r_from, W), r_to):
            for side, d, best in ((BID, 1, bbi), (ASK, -1, bai)):
                c = best[r]
                if c < 0:
                    continue
                hold = best[r - W + 1:r + 1]
                gave_way = hold.min() < c if d == 1 else ((hold < 0).any() or hold.max() > c)
                if gave_way:
                    continue                                    # the price gave way inside the window
                filled = grids[side][r + 1, c] - grids[side][r - W + 1, c]
                vis = (book.bid if side == BID else book.ask)[r - W + 1:r + 1, c].max()
                if filled < 2 * max(vis, 1) or filled < 2 * typ:
                    continue
                key = (side, c)
                if r - last_sig.get(key, -10 ** 9) < 300:
                    continue
                last_sig[key] = r
                t = float(book.t0 + r + 1)
                p0 = px_at(dt, t)
                P = book.lo + c
                stop_ticks = max((p0 - (P - 2 * d)) * d, 1)
                for side_name, dd in (("as usually traded", d), ("random side", int(RNG.choice([-1, 1])))):
                    st = stop_ticks if dd == d else stop_ticks
                    for ex_name, sp, tp, mv in (("stop 2 ticks through, 2x target", st * TICK, 2 * st * TICK, None),
                                                ("owner's bracket", S["bot"]["stop_points"], S["bot"]["target_points"], 7.0)):
                        mvto = 1.0 if mv else None
                        gr = bracket_exit(dt, t, p0, dd, flat, sp, tp, mv, mvto, slip=0)[0]
                        nt = bracket_exit(dt, t, p0 + dd * SLIP_TICKS, dd, flat, sp, tp, mv, mvto)[0]
                        out.append(row("3 iceberg / refill", ex_name, day, gr, nt, side_name))
        print(day, len(last_sig), flush=True)
        del book
    x = pd.DataFrame(out)
    x.to_csv(OUT / "strategy_natural_iceberg.csv", index=False)
    print(summary(x).round(2).to_string())


def summary(x: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (s_, v), g in x.groupby(["strategy", "variant"]):
        a, r = g[g["side"] != "random side"], g[g["side"] == "random side"]
        yr = a.groupby(a["day"].str[:4])["net_usd"].mean()
        rows.append(dict(strategy=s_, variant=v, trades=len(a), win_rate=(a["net_usd"] > 0).mean(),
                         gross_points=a["gross_points"].mean(), net_usd=a["net_usd"].mean(),
                         ci95=1.96 * a["net_usd"].std() / np.sqrt(max(len(a), 1)),
                         random_side_gross=r["gross_points"].mean(), random_side_net=r["net_usd"].mean(),
                         by_year=" ".join(f"{y}: {v_:+.0f}" for y, v_ in yr.items())))
    return pd.DataFrame(rows)


def main() -> None:
    b = all_bars()
    x = pd.DataFrame(momentum(b) + sweeps_bars(b) + ticks_part(b))
    x.to_csv(OUT / "strategy_natural.csv", index=False)
    pd.set_option("display.width", 260)
    print(summary(x).round(2).to_string())


if __name__ == "__main__":
    import sys
    iceberg() if "--iceberg" in sys.argv else main()
