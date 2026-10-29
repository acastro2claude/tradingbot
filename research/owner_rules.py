"""The owner's reasons from the blind review, turned into rules and tested on every ES level test.

From the owner's answers on the 12 review charts (review/review_answers.csv):
  - other side still in control -> skip  (#2, #6, #8, #11: "delta still making lower
    lows", "buyers still too aggressive"):
      control:    the 5-minute delta extreme against the trade was set in the last 30 s
      aggression: the other side's aggressive volume over the last minute >= 1.2x ours
  - weaker retest -> take  (#3: "aggressive buys slowed since the last attempt at this
    level"): the level was tested earlier (within an hour) and the aggressive volume
    pushing into it in the 2 minutes before arrival is at most 80% of last time
  - absorption spike -> take  (#9: "spike in aggressive buyers but price does not push
    past the POC"): a 15-second bar of aggressive volume into the level at least 2x the
    median bar of the prior 15 minutes, while price got no more than 1 point past it
  - delta divergence -> take  (#7, #1: "delta makes a lower low while price is at the
    previous high"): price at (within 1 point) or past the 30-minute high (low for a
    long) while cumulative delta is below its value at that earlier high (above at the low)
  - zone conflict -> skip  (#12: "the rule is we go short at supply zones"): no long
    with a live supply zone within 4 points above, no short with a demand zone within
    4 points below
Targets (owner): the next POC in the trade's direction, 4-20 points away (else 2x the
risk), with a 3-point stop. The 2-point stop / 4-point target is shown too.

All numbers are in [owner_rules] (settings.toml), proposals until the owner settles them.
Needs data/derived/es_weeks_trades and level_tests_es_weeks.csv (research.bot_week_study).
Run from the repo root:   python -m research.owner_rules
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.backtest import simulate_trade
from research.bot_week_study import CONTRACTS, ES_POINT, FLAT_ET, SLIP_TICKS, TICK, TRADES_DIR, et
from research.journal_replay import es_context
from research.journal_study import OUT
from research.level_test_book import marked_levels
from research.market_data import session_dates
from research.paths import trade_path
from research.settings import load_settings

S = load_settings()
R = S["owner_rules"]


def tk(points: float) -> int:
    return int(round(points / TICK))


class DayTrades:
    def __init__(self, es: str, day: str):
        tr = pd.read_parquet(TRADES_DIR / f"{es}_{day}.parquet").sort_values("t", kind="stable")
        self.t = tr["t"].to_numpy()
        self.px = tr["price"].to_numpy().astype(np.int64)
        size = tr["size"].to_numpy()
        side = tr["side"].to_numpy()
        self.buy = np.where(side == "B", size, 0)
        self.sell = np.where(side == "A", size, 0)
        self.cvd = np.cumsum(self.buy - self.sell)
        self.cum_buy = np.concatenate([[0], np.cumsum(self.buy)])
        self.cum_sell = np.concatenate([[0], np.cumsum(self.sell)])
        self.p, self.pt = trade_path(self.px, self.t)

    def idx(self, a: float, b: float) -> tuple[int, int]:
        i, j = np.searchsorted(self.t, [a, b], "left")
        return int(i), int(j)

    def volume(self, a: float, b: float, buyers: bool) -> int:
        i, j = self.idx(a, b)
        c = self.cum_buy if buyers else self.cum_sell
        return int(c[j] - c[i])


def features(dt: DayTrades, r, prev, zones_now: pd.DataFrame) -> dict:
    d = int(r.direction)
    te, t0 = r.entry_time, r.time
    attackers_buy = d == -1                 # a short's level is attacked by buyers, a long's by sellers
    out = {}

    # other side still in control
    i, j = dt.idx(te - R["control_lookback_s"], te)
    if j > i:
        c = dt.cvd[i:j] * d                 # in the trade's favour
        k = int(np.argmin(c))
        out["control"] = dt.t[i + k] >= te - R["control_recent_s"]
    else:
        out["control"] = False
    ours = dt.volume(te - R["aggression_window_s"], te, buyers=(d == 1))
    theirs = dt.volume(te - R["aggression_window_s"], te, buyers=(d == -1))
    out["aggression"] = theirs >= R["aggression_ratio"] * max(ours, 1)

    # weaker retest
    out["retest"] = prev is not None
    if prev is not None:
        now = dt.volume(t0 - R["retest_window_s"], t0, buyers=attackers_buy)
        before = dt.volume(prev - R["retest_window_s"], prev, buyers=attackers_buy)
        out["weaker_retest"] = now <= R["retest_weaker_ratio"] * max(before, 1)
    else:
        out["weaker_retest"] = False

    # absorption spike
    bar = R["spike_bar_s"]
    base_from = t0 - R["spike_baseline_min"] * 60
    edges = np.arange(base_from, te + bar, bar)
    vols = np.array([dt.volume(a, a + bar, buyers=attackers_buy) for a in edges[:-1]])
    n_base = int((t0 - base_from) // bar)
    base = np.median(vols[:n_base]) if n_base > 0 else 0
    test_bars = vols[max(n_base - 4, 0):]                  # from a minute before the test to the entry
    through = (int(r.edge) - int(r.test_extreme)) * d      # ticks price got past the level's edge
    out["spike"] = bool(len(test_bars) and base > 0 and test_bars.max() >= R["spike_multiple"] * base
                        and through <= tk(R["spike_max_through_points"]))

    # delta divergence at the 30-minute extreme
    i, j = dt.idx(te - R["divergence_lookback_min"] * 60, t0)
    if j - i > 10:
        win_px = dt.px[i:j] * -d            # higher = more extreme against a fade (a high for a short)
        k = int(np.argmax(win_px))
        prior_ext = dt.px[i + k]
        ki, kj = dt.idx(t0, te)
        seg = dt.px[ki:kj + 1]
        test_ext = (seg.max() if d == -1 else seg.min()) if len(seg) else int(r.test_extreme)
        at_ext = (test_ext - prior_ext) * -d >= -tk(R["divergence_near_points"])
        ke = ki + int(np.argmax(seg * -d)) if len(seg) else ki
        out["divergence"] = bool(at_ext and (dt.cvd[min(ke, len(dt.cvd) - 1)] - dt.cvd[i + k]) * -d < 0)
    else:
        out["divergence"] = False

    # zone conflict
    if len(zones_now):
        if d == 1:
            z = zones_now[(zones_now["direction"] == -1) & (zones_now["lo"] >= r.entry_price)
                          & (zones_now["lo"] - r.entry_price <= tk(R["zone_conflict_points"]))]
        else:
            z = zones_now[(zones_now["direction"] == 1) & (zones_now["hi"] <= r.entry_price)
                          & (r.entry_price - zones_now["hi"] <= tk(R["zone_conflict_points"]))]
        out["zone_conflict"] = len(z) > 0
    else:
        out["zone_conflict"] = False
    return out


def trade(dt: DayTrades, r, pocs: np.ndarray, flat: float, stop_pts: float, target: str) -> float:
    d = int(r.direction)
    fill = int(r.entry_price) + d * SLIP_TICKS
    stop = fill - d * tk(stop_pts)
    if target == "next POC":
        ahead = (pocs - fill) * d
        ok = pocs[(ahead >= tk(R["target_min_points"])) & (ahead <= tk(R["target_max_points"]))]
        ft = int(ok[np.argmin((ok - fill) * d)]) if len(ok) else fill + d * 2 * tk(stop_pts)
    else:
        ft = fill + d * tk(4)
    i0 = int(np.searchsorted(dt.pt, r.entry_time, "right"))
    res = simulate_trade(dt.p, dt.pt, i0, fill, d, stop, ft, ft, flat, SLIP_TICKS, contracts=CONTRACTS,
                         first_contracts=CONTRACTS)
    return res.points / CONTRACTS


def patient_entry(dt: DayTrades, t0: float, d: int, edge: int) -> tuple[float, int] | None:
    """First second after the test start when the owner's 'wait' is over (see settings), or None."""
    B = S["book_study"]
    end = t0 + R["wait_max_min"] * 60
    i0, i1 = dt.idx(t0, end)
    if i1 <= i0:
        return None
    seg = dt.px[i0:i1]
    broke = np.nonzero((edge - seg) * d >= tk(B["test_break_points"]))[0]
    stop_at = dt.t[i0 + broke[0]] if len(broke) else end
    for tau in np.arange(t0 + 5, stop_at, 1.0):
        j = int(np.searchsorted(dt.t, tau, "left"))
        if j <= i0:
            continue
        now = dt.px[j - 1]
        ext = dt.px[i0:j].min() if d == 1 else dt.px[i0:j].max()
        if (now - ext) * d < tk(B["turn_points"]) or abs(now - edge) > tk(R["near_level_points"]):
            continue
        a, b = dt.idx(tau - R["control_lookback_s"], tau)
        c = dt.cvd[a:b] * d
        if len(c) and dt.t[a + int(np.argmin(c))] >= tau - R["control_recent_s"]:
            continue
        ours = dt.volume(tau - R["aggression_window_s"], tau, buyers=(d == 1))
        theirs = dt.volume(tau - R["aggression_window_s"], tau, buyers=(d == -1))
        if ours < theirs:
            continue
        if j >= len(dt.px):
            return None
        return float(dt.t[j]), int(dt.px[j])       # market order: the next trade's price (+ slippage later)
    return None


def main() -> None:
    tests = pd.read_csv(OUT / "level_tests_es_weeks.csv")
    plan = pd.read_csv(OUT / "es_week_price_plan.csv").set_index("day")
    sessions, zones, offsets = es_context(S)
    cost = CONTRACTS * S["backtest"]["commission_per_contract_round_trip"]
    rows = []
    for day, g in tests.groupby("day"):
        es = plan.loc[day, "es"]
        dt = DayTrades(es, day)
        session = pd.Timestamp(session_dates(pd.Series([pd.Timestamp(et(day, "12:00"), unit="s", tz="UTC")])).iat[0])
        flat = et(day, FLAT_ET)
        for r in g[g["turn"] == "turned"].itertuples():
            earlier = g[(g["edge"] == r.edge) & (g["direction"] == r.direction) & (g["time"] < r.time)
                        & (g["time"] >= r.time - R["retest_within_min"] * 60)]
            prev = float(earlier["time"].max()) if len(earlier) else None
            lv = marked_levels(sessions, zones, session, int(offsets[es]), r.entry_time, r.entry_time,
                               S["bot"]["recent_poc_lookback_sessions"])
            pocs = lv.loc[lv["source"] == "POC", "edge"].to_numpy() if len(lv) else np.array([])
            zones_now = lv[lv["source"] == "zone"] if len(lv) else lv
            f = features(dt, r, prev, zones_now)
            rows.append(dict(test=r.Index, day=day, entry_time=r.entry_time, direction=r.direction, **f,
                             pts_2_4=trade(dt, r, pocs, flat, 2, "fixed 4"),
                             pts_poc=trade(dt, r, pocs, flat, R["stop_points"], "next POC")))
    x = pd.DataFrame(rows)
    x.to_csv(OUT / "owner_rules_tests.csv", index=False)

    # patient entry on every test (whether or not it turned at once)
    prow = []
    for day, g in tests.groupby("day"):
        es = plan.loc[day, "es"]
        dt = DayTrades(es, day)
        session = pd.Timestamp(session_dates(pd.Series([pd.Timestamp(et(day, "12:00"), unit="s", tz="UTC")])).iat[0])
        flat = et(day, FLAT_ET)
        lv = marked_levels(sessions, zones, session, int(offsets[es]), et(day, "09:30"), et(day, "16:00"),
                           S["bot"]["recent_poc_lookback_sessions"])
        pocs = lv.loc[lv["source"] == "POC", "edge"].to_numpy() if len(lv) else np.array([])
        for r in g.itertuples():
            pe = patient_entry(dt, r.time, int(r.direction), int(r.edge))
            if pe is None:
                prow.append(dict(test=r.Index, day=day, entered=False))
                continue
            t_in, price = pe
            rr = r._replace(entry_time=t_in, entry_price=price)
            prow.append(dict(test=r.Index, day=day, entered=True, wait_s=t_in - r.time,
                             first_turn=r.turn == "turned",
                             pts_2_4=trade(dt, rr, pocs, flat, 2, "fixed 4"),
                             pts_poc=trade(dt, rr, pocs, flat, R["stop_points"], "next POC")))
    pt = pd.DataFrame(prow)
    pt.to_csv(OUT / "owner_rules_patient.csv", index=False)
    e = pt[pt["entered"]]
    print(f"patient entry: {len(e)} of {len(pt)} tests entered, median wait {e['wait_s'].median():.0f} s")
    for col, label in (("pts_2_4", "2/4"), ("pts_poc", "3-pt stop, next POC")):
        print(f"  {label}: win {(e[col] > 0).mean():.0%}, $/trade {e[col].mean() * ES_POINT - cost:,.0f}")

    rules = {
        "every test": np.ones(len(x), bool),
        "skip: other side in control (delta)": ~x["control"],
        "skip: other side more aggressive (1 min)": ~x["aggression"],
        "skip: zone conflict": ~x["zone_conflict"],
        "take: weaker retest": x["weaker_retest"],
        "take: absorption spike": x["spike"],
        "take: delta divergence": x["divergence"],
        "owner filter: no control, no aggression, no zone conflict":
            ~x["control"] & ~x["aggression"] & ~x["zone_conflict"],
        "owner filter + (weaker retest or spike or divergence)":
            ~x["control"] & ~x["aggression"] & ~x["zone_conflict"] & (x["weaker_retest"] | x["spike"] | x["divergence"]),
    }
    out = []
    for name, m in rules.items():
        m = np.asarray(m, bool)
        g = x[m]
        row = dict(rule=name, tests=len(g))
        for col, label in (("pts_2_4", "2/4"), ("pts_poc", "3-pt stop, next POC")):
            row[f"win {label}"] = (g[col] > 0).mean() if len(g) else np.nan
            row[f"$/trade {label}"] = g[col].mean() * ES_POINT - cost if len(g) else np.nan
        out.append(row)
    table = pd.DataFrame(out)
    table.to_csv(OUT / "owner_rules_summary.csv", index=False)

    # how the rules would have called the 12 review charts, next to the owner
    key = pd.read_csv(OUT / "review" / "review_answers.csv")[["review", "owner"]]
    rk = pd.read_csv(OUT / "review" / "review_key.csv")[["review", "day", "entry_time"]].merge(key, on="review")
    rk = rk.merge(x, on=["day", "entry_time"], how="left")
    rk["filter"] = np.where(~rk["control"] & ~rk["aggression"] & ~rk["zone_conflict"], "pass", "skip")
    rk["with a take signal"] = np.where((rk["filter"] == "pass") & (rk["weaker_retest"] | rk["spike"] | rk["divergence"]),
                                        "take", "skip")
    rk.to_csv(OUT / "owner_rules_review.csv", index=False)

    pd.set_option("display.width", 250)
    print(table.round(2).to_string())
    print(rk[["review", "owner", "control", "aggression", "zone_conflict", "weaker_retest", "spike", "divergence",
              "filter", "with a take signal"]].to_string())


if __name__ == "__main__":
    main()
