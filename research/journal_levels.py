"""Are the owner's entries at classic day-trading levels, or right after bursts?

Compares the owner's journal entries (corrected times) with random moments in the
same windows: distance to yesterday's regular-hours high, low and close, the
overnight high and low, the opening 15-minute range, the day's high and low so
far, round numbers, and how recently a fitting 100+ absorption burst printed
within 2 points. Writes research/results/journal/replay_levels.csv.

Run from the repo root:   python -m research.journal_levels
"""
import numpy as np, pandas as pd
from research.settings import load_settings
from research.level_study import prepare
from research.journal_study import ES_BARS, ES_SYMBOLOGY, OUT
from research.journal_replay import CONDITION_ES, MES_DIR, load_day
from research.market_data import epoch_seconds, session_dates
s = load_settings()
fb, sessions, _ = prepare(s, ES_BARS, ES_SYMBOLOGY, ("2024-01-16", "2025-03-21"), CONDITION_ES)
fb["t"] = epoch_seconds(fb["ts"])
et = fb["ts"].dt.tz_convert("America/New_York"); fb["mins"] = et.dt.hour * 60 + et.dt.minute
by_sess = {k: g for k, g in fb.groupby("session")}
order = list(sessions.index)

def levels_at(sess, when, contract):
    """Classic levels (raw ticks of `contract`) known at time `when`."""
    if sess not in by_sess: return {}
    cur = by_sess[sess]; cur = cur[cur["t"] < when - 60]
    out = {}
    k = order.index(sess)
    if k > 0 and order[k-1] in by_sess:
        prev = by_sess[order[k-1]]
        rth = prev[(prev.mins >= 570) & (prev.mins < 960)]
        if len(rth):
            out["prior day high"] = rth.high.max(); out["prior day low"] = rth.low.min()
            out["prior day close"] = rth.close.iloc[-1]
            # prior session prices are in the previous contract's raw prices; same contract unless a roll
            if sessions.loc[order[k-1], "contract"] != sessions.loc[sess, "contract"]:
                gap = sessions.loc[sess, "roll_gap_ticks"]
                for key in ("prior day high", "prior day low", "prior day close"): out[key] += gap
    on = cur[cur.mins < 570] if len(cur) else cur
    if len(on): out["overnight high"], out["overnight low"] = on.high.max(), on.low.min()
    orng = cur[(cur.mins >= 570) & (cur.mins < 585)]
    if len(orng) and when >= orng["t"].max() + 60: out["opening 15-min high"], out["opening 15-min low"] = orng.high.max(), orng.low.min()
    rth_now = cur[cur.mins >= 570]
    if len(rth_now): out["day high so far"], out["day low so far"] = rth_now.high.max(), rth_now.low.min()
    return {k: v for k, v in out.items()}   # raw ticks of the session's front contract

t = pd.read_csv(OUT / "replay_trades.csv")
for c in ("entry_time",): t[c] = pd.to_datetime(t[c], utc=True)
t["t_in"] = epoch_seconds(t["entry_time"]); t["e"] = np.rint(t["entry"] / 0.25).astype(int)
t["d"] = np.where(t["side"] == "long", 1, -1)
rng = np.random.default_rng(7); rows = []
for (day, mes), g in t.groupby(["day", "mes"]):
    m = load_day(MES_DIR / f"{mes}_{day}.dbn.zst", s)
    b = m["bursts"]; b100 = b[b.contracts >= 100]
    rt = np.sort(rng.uniform(m["tt"][0] + 300, m["tt"][-1], 20)); rp = m["px"][np.searchsorted(m["tt"], rt, "right") - 1]
    rd = rng.choice([-1, 1], 20)
    for kind, T, P, D in (("yours", g["t_in"].to_numpy(), g["e"].to_numpy(), g["d"].to_numpy()), ("random", rt, rp, rd)):
        for tt0, pp, dd in zip(T, P, D):
            sess = session_dates(pd.Series([pd.Timestamp(tt0, unit="s", tz="UTC")])).iat[0]
            lv = levels_at(sess, tt0, mes)
            fit = b100[(b100.direction == dd) & (b100.end_time <= tt0) & ((b100.price - pp).abs() <= 8)]
            since = tt0 - fit.end_time.max() if len(fit) else np.inf
            row = dict(kind=kind, since_burst=since, round5=min(pp % 20, 20 - pp % 20), round10=min(pp % 40, 40 - pp % 40))
            for name, L in lv.items(): row[name] = abs(pp - L)
            dhi, dlo = lv.get("day high so far"), lv.get("day low so far")
            row["at day extreme (short at high / long at low)"] = (dd == -1 and dhi is not None and pp >= dhi - 4) or (dd == 1 and dlo is not None and pp <= dlo + 4)
            rows.append(row)
r = pd.DataFrame(rows)
y, x = r[r.kind == "yours"], r[r.kind == "random"]
def pct(df, f): return f"{f(df).mean()*100:4.0f}%"
checks = {
    "fitting 100+ burst within 2 pts in the last 10 s": lambda d: d.since_burst <= 10,
    "fitting 100+ burst within 2 pts in the last 60 s": lambda d: d.since_burst <= 60,
    "within 1 pt of a 5-point round number": lambda d: d.round5 <= 4,
    "within 1 pt of a 10-point round number": lambda d: d.round10 <= 4,
    "short at / long at the day's extreme so far (within 1 pt)": lambda d: d["at day extreme (short at high / long at low)"],
}
for name in ["prior day high", "prior day low", "prior day close", "overnight high", "overnight low",
             "opening 15-min high", "opening 15-min low", "day high so far", "day low so far"]:
    checks[f"within 2 pts of {name}"] = (lambda n: (lambda d: d[n] <= 8))(name)
checks["within 2 pts of ANY of these levels"] = lambda d: (d[[c for c in d.columns if c.startswith(("prior","overnight","opening","day "))]] <= 8).any(axis=1)
out = pd.DataFrame({"your entries": {n: f(y).mean() for n, f in checks.items()},
                    "random moments, same windows": {n: f(x).mean() for n, f in checks.items()}})
out.to_csv(OUT / "replay_levels.csv")
print((out * 100).round(0).to_string())
