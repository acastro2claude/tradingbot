"""The owner's entry read at a zone test, coded from the tape (trades with the buyer/seller side).

From the owner's notes on the replay page (Oct 2026). Long at a demand zone (supply is the mirror):
  A  "absorption, then buyers come in": a burst of aggressive selling at the zone (the owner's
     absorption markers), price fails to go more than 1 point below it, then buyers' aggressive
     volume over the last 20 s is at least 1.2x the sellers' with delta up, and price has turned
     1 point off the low.
  B  "retest with a CVD higher low" / "weaker retest": after a first low and a 1-point bounce, price
     comes back to within 1.5 points of that low (at most 1 point lower). On that second push, either
     the CVD low is higher than at the first low, or the aggressive selling into it is less than 0.7x
     the first push. Entry once price turns 1 point off the second low and (version 2) buyers have
     come in as in A ("sellers in control" was the owner's most common reason to skip).
  The read ends without a trade if price gets 2 points past the zone's far edge ("sellers in
  control") or 5 minutes pass. Entry = the last price at that second + 1 tick.
Filters from the owner's skip reasons: no entries before 10:00 ET; none within 30 minutes after a
news volume spike (08:30, 10:00 or 14:00 ET minute at 3x the 10 minutes before); no trading on Fed
days. (Version 1 also skipped zones price had traded 1 point through or hung in for 30 minutes since
they formed; that removed the owner's 5 best replay trades, so it is off.)
Settings: [entry_read]. Everything there is a proposal.

Checked against the owner's replay decisions:   python -m research.entry_read
Writes research/results/journal/entry_read_replay.csv and entry_read_replay_summary.csv.
"""

from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd

from research.absorption import find_bursts
from research.bot_week_study import CONTRACTS, ES_POINT, FLAT_ET, SLIP_TICKS, et
from research.journal_study import OUT
from research.level_study import DERIVED
from research.level_test_book import TestReader
from research.owner_rules import TRADES_DIR, DayTrades
from research.replay_score import DECISIONS, TICK, fixed_exit_t, sample
from research.settings import load_settings

S = load_settings()
E = S["entry_read"]


def ticks(points: float) -> int:
    return int(round(points / TICK))


def hm(text: str) -> int:
    h, m = map(int, text.split(":"))
    return h * 60 + m


def read_test(dt: DayTrades, bursts: pd.DataFrame, T: float, d: int, near: int, far: int) -> dict | None:
    """The first entry the read gives at one zone test, or None. Prices in ticks, times in seconds."""
    t0, t1 = T - E["look_back_seconds"], T + E["window_seconds"]
    secs = np.arange(int(t0), int(t1) + 1)
    i = np.searchsorted(dt.t, secs, "left")                  # trades before each second start
    if i[-1] <= i[0]:
        return None
    y = dt.px * d                                            # long view: higher y = better for the trade
    push = dt.sell if d == 1 else dt.buy                     # aggressive volume pushing into the zone
    other = dt.buy if d == 1 else dt.sell
    cp, co = np.concatenate([[0], np.cumsum(push)]), np.concatenate([[0], np.cumsum(other)])
    cvd = co - cp                                            # delta in the trade's favour
    nearY, farY = near * d, far * d
    bz = bursts[(bursts["direction"] == d) & (bursts["time"] >= t0) & (bursts["time"] <= t1)]
    bz = bz[(bz["price"] * d <= nearY + ticks(E["burst_zone_points"]))
            & (bz["price"] * d >= farY - ticks(E["burst_zone_points"]))]
    turn, brk, hold = ticks(E["turn_points"]), ticks(E["break_points"]), ticks(E["hold_points"])
    bounce, retest = ticks(E["bounce_points"]), ticks(E["retest_points"])
    k_t = int(np.searchsorted(secs, T))
    low, low_k = np.inf, None                                # lowest y since the test, and its trade index
    phase, L1 = "push1", None
    for s_ in range(k_t, len(secs) - 1):
        a, b = i[s_], i[s_ + 1]
        if b <= a:
            continue
        seg = y[a:b]
        m = int(np.argmin(seg))
        if seg[m] < low:
            low, low_k = seg[m], a + m
        if low <= farY - brk:
            return None                                       # through the zone: the other side is in control
        now, last = secs[s_ + 1], y[b - 1]
        w0 = int(np.searchsorted(dt.t, now - E["other_side_seconds"]))
        o, p = co[b] - co[w0], cp[b] - cp[w0]
        other_in = o > p and o >= E["other_side_ratio"] * p            # buyers have come in over the last 20 s
        # A: absorption at the zone, sellers fail, buyers come in
        bb = bz[bz["time"] <= now]
        if len(bb):
            bp = int(bb["price"].iloc[-1]) * d
            if low >= bp - hold and other_in and last - low >= turn:
                return dict(signal="A absorption then other side", entry_time=float(now), entry_y=int(last))
        # B: retest with a CVD higher low or a weaker push
        if phase == "push1" and last - low >= bounce:
            L1, C1 = low, cvd[low_k + 1]
            S1 = cp[low_k + 1] - cp[int(np.searchsorted(dt.t, t0))]
            phase, top_k = "bounced", b
        elif phase == "bounced":
            if y[top_k - 1] < last:
                top_k = b
            if last <= L1 + retest and seg.min() >= L1 - hold:
                phase, L2, L2_k = "retest", seg.min(), a + int(np.argmin(seg))
        elif phase == "retest":
            if seg.min() < L2:
                L2, L2_k = seg.min(), a + int(np.argmin(seg))
            if L2 < L1 - hold:                                # a lower low: a new push, start over from it
                phase, low, low_k = "push1", L2, L2_k
                continue
            if last - L2 >= turn and (other_in or not E["other_side_for_retest"]):
                C2 = cvd[L2_k + 1]
                S2 = cp[L2_k + 1] - cp[top_k]
                if C2 > C1 or S2 < E["weaker_push_ratio"] * S1:
                    why = "B retest, CVD higher low" if C2 > C1 else "B retest, weaker push"
                    return dict(signal=why, entry_time=float(now), entry_y=int(last))
                L1, C1, S1, phase, top_k = L2, C2, S2, "bounced", b   # no divergence: this low is the new reference
    return None


def news_blocks(minute_volume: pd.Series) -> list[tuple[int, int]]:
    """Blocked minutes of the day (ET minute of day) after news volume spikes. minute_volume: index HH:MM."""
    out = []
    for m_ in E["news_minutes"]:
        if m_ not in minute_volume.index:
            continue
        x = hm(m_)
        prev = [f"{(x - k) // 60:02d}:{(x - k) % 60:02d}" for k in range(1, 11)]
        base = minute_volume.reindex(prev).median()
        if base and base > 0 and minute_volume[m_] >= E["news_spike_ratio"] * base:
            out.append((x, x + E["news_block_minutes"]))
    return out


def day_trades_df(market: str, contract: str, day: str) -> pd.DataFrame:
    if market == "ES":
        return pd.read_parquet(TRADES_DIR / f"{contract}_{day}.parquet")
    return pd.read_parquet(DERIVED / "replay_trades" / f"{contract}_{day}.parquet")


def filters(day: str, T: float, minute_vol: pd.Series, through: float, inside: float) -> str | None:
    """The owner's reasons to skip before reading the tape, or None."""
    loc = pd.Timestamp(T, unit="s", tz="UTC").tz_convert("America/New_York")
    mins = loc.hour * 60 + loc.minute
    if day in E["fed_days"]:
        return "Fed day"
    if mins < hm(E["entry_from_et"]):
        return "before 10:00"
    if any(a <= mins < b for a, b in news_blocks(minute_vol)):
        return "after a news spike"
    if E["use_zone_invalid_filter"] and (through >= E["invalid_through_points"] or inside >= E["invalid_inside_minutes"]):
        return "zone already traded through or hung in"
    return None


def main() -> None:
    from research.journal_study import ES_BARS, ES_SYMBOLOGY
    from research.market_data import load_bars

    cost = CONTRACTS * S["backtest"]["commission_per_contract_round_trip"]
    stop, target = S["bot"]["stop_points"], S["run_winners"]["target_points"]
    R = S["replay"]
    bars = load_bars(ES_BARS, ES_SYMBOLOGY)
    bt = bars["ts"].dt.tz_convert("America/New_York")
    vol = bars.groupby([bt.dt.date.astype(str), bt.dt.strftime("%H:%M")])["volume"].sum()

    dec = pd.DataFrame([json.load(open(f)) for f in sorted(glob.glob(str(DECISIONS / "*.json")))])
    d = dec.merge(pd.read_csv(OUT / "replay_clip_ids.csv"), left_on="clip_id", right_on="public_id")
    d = d.merge(pd.read_csv(OUT / "replay_tests.csv"), on="id")
    d = d[d["decision"].isin(["long", "short", "skip", "unsure"])]
    d = d.sort_values("round").drop_duplicates("id", keep="last")
    inv = pd.read_csv(OUT / "replay_invalid.csv").set_index("test_id")
    rows = []
    for (market, contract, day), g in d.groupby(["market", "contract", "day"]):
        tr = day_trades_df(market, contract, day)
        dt = DayTrades(trades=tr)
        bmin = R["burst_min_es"] if market == "ES" else R["burst_min_mes"]
        bursts = find_bursts(tr["t"].to_numpy(), tr["price"].to_numpy().astype(np.int64), tr["size"].to_numpy(),
                             tr["side"].to_numpy(), R["burst_seconds"], bmin)
        bursts["direction"] = np.where(bursts["side"] == "B", -1, 1)        # buyers absorbed -> short side
        flat = et(day, FLAT_ET)
        mv = vol.loc[day] if day in vol.index.get_level_values(0) else pd.Series(dtype=float)
        for r in g.itertuples():
            zd = int(r.direction)
            far = int(r.lo) if zd == 1 else int(r.hi)
            smp = sample(r.saved_at, r.round)
            quick = smp == "new clips, second sitting" and not str(r.note or "").strip()
            row = dict(clip=r.clip_id, market=market, day=day, owner=r.decision, careful=not quick, zone_dir=zd,
                       tf=r.tf, zone_test=r.zone_test)
            iv = inv.loc[r.id] if r.id in inv.index else None
            why = filters(day, r.time, mv, iv["through_after_pts"] if iv is not None else 0.0,
                          iv["minutes_inside"] if iv is not None else 0.0)
            row["filter"] = why
            got = read_test(dt, bursts, r.time, zd, int(r.edge), far)
            row["read_signal"] = got["signal"] if got else None
            row["read_takes"] = bool(got) and why is None
            ex = {}
            if got:
                fill = got["entry_y"] * zd + zd * SLIP_TICKS
                pts, _ = fixed_exit_t(dt, got["entry_time"], fill, zd, flat, stop, target)
                ex["read"] = pts * TICK
                row["read_vs_test_s"] = got["entry_time"] - r.time
            if r.decision in ("long", "short"):
                od = 1 if r.decision == "long" else -1
                pts, _ = fixed_exit_t(dt, r.entry_time, round(r.entry_price / TICK) + od * SLIP_TICKS, od, flat, stop, target)
                ex["owner"] = pts * TICK
                row["owner_vs_test_s"] = r.entry_time - r.time
            k, _ = TestReader.turn_trade(dt, r.time, zd, int(r.edge))
            if k is not None:
                pts, _ = fixed_exit_t(dt, float(dt.t[k]), int(dt.px[k]) + zd * SLIP_TICKS, zd, flat, stop, target)
                ex["first_turn"] = pts * TICK
            for kk, v in ex.items():
                row[f"{kk}_points"] = v
            rows.append(row)
    x = pd.DataFrame(rows)
    x["owner_takes"] = x["owner"].isin(["long", "short"])
    x.to_csv(OUT / "entry_read_replay.csv", index=False)
    usd = lambda v: v * S["market"]["point_value_usd"] * CONTRACTS - cost  # noqa: E731

    out = []
    for name, m in {"careful decisions": x["careful"], "all decisions": x["careful"].notna()}.items():
        y = x[m]
        for label, take in {"read (with filters)": y["read_takes"], "read (no filters)": y["read_signal"].notna(),
                            "filters only, first turn": y["filter"].isna() & y["first_turn_points"].notna()}.items():
            both = (take & y["owner_takes"]).sum()
            pts = y.loc[take, "read_points" if label.startswith("read") else "first_turn_points"]
            out.append(dict(sample=name, rule=label, clips=len(y), rule_takes=int(take.sum()),
                            owner_takes=int(y["owner_takes"].sum()), both_take=int(both),
                            share_of_owner_takes_matched=both / max(y["owner_takes"].sum(), 1),
                            share_of_rule_takes_owner_took=both / max(take.sum(), 1),
                            agreement=(take == y["owner_takes"]).mean(),
                            win_rate=(pts > 0).mean(), usd_per_trade=usd(pts).mean(), net_usd=usd(pts).sum()))
        o = y.loc[y["owner_takes"], "owner_points"].dropna()
        out.append(dict(sample=name, rule="the owner", clips=len(y), rule_takes=len(o), owner_takes=len(o),
                        win_rate=(o > 0).mean(), usd_per_trade=usd(o).mean(), net_usd=usd(o).sum()))
    t = pd.DataFrame(out)
    t.to_csv(OUT / "entry_read_replay_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(t.round(2).to_string())
    print(pd.crosstab(x.loc[x["careful"], "owner_takes"], x.loc[x["careful"], "read_takes"], margins=True))
    print(x.loc[x["careful"]].groupby("filter", dropna=False)["owner_takes"].agg(["size", "sum"]))
    print(x.loc[x["careful"] & x["read_takes"]].groupby("read_signal")["owner_takes"].agg(["size", "sum"]))


if __name__ == "__main__":
    main()
