"""How a stop fills on MES: stop-market vs stop-limit (owner's question, Oct 2026).

Owner: "stop loss should probably be set as a stop limit order that way we reduce the chance for
slippage", "5 points hard limit for a loss". Settings: [stop_fills].

On every MES regular session with trades on disk (Apr-Oct 2026), random entries (random moment
10:00-15:30 ET, random side, at the last trade price) get a stop 4 or 5 points away. For each stop
that triggers (a trade at or through the stop price before 15:59):
  - stop-market: filled at the worst trade price within 5 ms of the trigger (the sweep the order
    joins). Slippage = ticks past the stop. Also shown: the worst price within 0.5 s (pessimistic).
  - stop-limit with the limit 0, 1, 2 or 4 ticks past the stop: filled at once if that sweep stayed
    at or inside the limit; otherwise it waits at the limit and fills only if price trades back
    through it (a print better than the limit) within 10 s. Otherwise it counts as unfilled: the
    position is still open, and we record how far price went past the stop in the next 60 s and
    where it was after 10 s.
Trade prints only, no order book: queue position is ignored, so fills are a little optimistic for the
limit order and pessimistic for the market order.

Writes research/results/journal/stop_fills.csv. Run:   python -m research.stop_fills
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.absorption_study import session_files
from research.bot_week_study import et
from research.journal_study import OUT
from research.market_data import epoch_seconds, load_trades
from research.settings import load_settings


def one_stop(t, px, i0: int, fill: int, d: int, stop_t: int, offsets, end: int, F: dict) -> dict | None:
    stop = fill - d * stop_t
    seg = px[i0:end]
    k = np.flatnonzero((seg - stop) * d <= 0)
    if not len(k):
        return None
    k = i0 + int(k[0])
    trig_t = float(t[k])
    worst = lambda a, b: int(px[a:b].min() if d == 1 else px[a:b].max())  # noqa: E731
    j_now = max(int(np.searchsorted(t, trig_t + F["instant_seconds"], "right")), k + 1)
    j_half = int(np.searchsorted(t, trig_t + F["sweep_seconds"], "right"))
    j10 = int(np.searchsorted(t, trig_t + F["come_back_seconds"], "right"))
    j60 = int(np.searchsorted(t, trig_t + 60, "right"))
    w_now = worst(k, j_now)
    row = dict(trigger_t=trig_t, trigger_gap=(stop - int(px[k])) * d, market_slip=(stop - w_now) * d,
               market_slip_half_s=(stop - worst(k, j_half)) * d)
    run = int(((stop - px[k:j60]) * d).max())                          # ticks past the stop in the next minute
    later = px[j10 - 1] if j10 > k else px[k]
    for off in offsets:
        limit = stop - d * off
        if (w_now - limit) * d >= 0:                                       # the sweep never went past the limit
            filled = True
        else:                                                              # waits; fills if price trades back through it
            filled = bool(len(np.flatnonzero((px[j_now:j10] - limit) * d > 0)))
        row[f"filled_{off}"] = filled
        row[f"run_if_unfilled_{off}"] = np.nan if filled else run
        row[f"loss_if_unfilled_exit_at_10s_{off}"] = np.nan if filled else int((stop - later) * d)
    return row


def main() -> None:
    s = load_settings()
    F = s["stop_fills"]
    tick = s["market"]["tick_size"]
    rng = np.random.default_rng(s["level_study"]["random_seed"])
    rows = []
    for day, contract, f in session_files():
        tr = load_trades(f, s)
        t, px = epoch_seconds(tr["ts"]), tr["price"].to_numpy().astype(np.int64)
        d_str = day.date().isoformat()
        a, b, flat = et(d_str, "10:00"), et(d_str, "15:30"), et(d_str, "15:59")
        if not (t[0] < a and t[-1] > b):
            continue
        end = int(np.searchsorted(t, flat, "right"))
        for t0 in rng.uniform(a, b, F["entries_per_day"]):
            i0 = int(np.searchsorted(t, t0, "right"))
            d = int(rng.choice([-1, 1]))
            for st in F["stops_points"]:
                r = one_stop(t, px, i0, int(px[i0 - 1]), d, round(st / tick), F["limit_offsets_ticks"], end, F)
                if r is not None:
                    rows.append(dict(day=d_str, stop_points=st, **r))
    x = pd.DataFrame(rows)
    x.to_csv(OUT / "stop_fills.csv", index=False)
    out = []
    for st, g in x.groupby("stop_points"):
        row = dict(stop_points=st, stops_hit=len(g), days=g["day"].nunique(),
                   market_avg_slip_ticks=g["market_slip"].mean(), market_slip_0=(g["market_slip"] <= 0).mean(),
                   market_slip_1=(g["market_slip"] == 1).mean(), market_slip_2plus=(g["market_slip"] >= 2).mean(),
                   market_slip_4plus=(g["market_slip"] >= 4).mean(), market_slip_max=g["market_slip"].max(),
                   market_slip_half_s_avg=g["market_slip_half_s"].mean(),
                   market_slip_half_s_2plus=(g["market_slip_half_s"] >= 2).mean(),
                   trigger_gapped=(g["trigger_gap"] > 0).mean())
        for off in F["limit_offsets_ticks"]:
            un = ~g[f"filled_{off}"]
            row[f"limit+{off}: unfilled"] = un.mean()
            row[f"limit+{off}: unfilled, median run in next 60s (ticks)"] = g.loc[un, f"run_if_unfilled_{off}"].median()
            row[f"limit+{off}: unfilled, worst run (ticks)"] = g.loc[un, f"run_if_unfilled_{off}"].max()
            row[f"limit+{off}: unfilled, ticks past stop at 10s (median)"] = g.loc[un, f"loss_if_unfilled_exit_at_10s_{off}"].median()
        out.append(row)
    t = pd.DataFrame(out).T
    print(t.round(4).to_string())


if __name__ == "__main__":
    main()
