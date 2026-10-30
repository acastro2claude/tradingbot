"""Day-level rules replayed on the owner's own journal (build step 2/3 side study, Oct 2026).

Takes the owner's 513 closed journal trades (Jan 2024 - Mar 2025, times corrected,
research/results/journal/journal_trades.csv) and asks: which day rules would have
helped? Each rule only decides which trades would have been taken; every trade keeps
its real result, costs included. Rules (grids in [journal_rules], settings.toml):
  - at most N trades a day
  - stop for the day after K losing trades
  - stop for the day once the day's net is down $L (daily loss limit)
  - stop for the day once the day's net is up $P (profit stop)
  - no entries in the first M minutes after the 09:30 ET open
  - no entries in the M minutes after a news release, found as a volume spike in the
    ES 1-minute bars at 08:30, 10:00 or 14:00 ET (that minute >= 3x the median of the
    10 minutes before; checked against CPI, jobs-report and Fed days)
  - mornings only (09:30-11:00 ET)
  - a few combinations
  - the first 3 trades of each day on sim (left out), or at half size: the owner's early
    trades had no edge and the later ones did
For each: trades, win rate, net $, $ per trade, profit factor, max drawdown, worst day,
winning days; the change against the journal as traded, in each half of the period, and
a 95% range for the change from resampling whole days.

Run from the repo root:   python -m research.journal_day_rules
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.journal_study import ES_BARS, ES_SYMBOLOGY, OUT
from research.settings import load_settings

S = load_settings()
J = S["journal_rules"]


def news_spikes() -> pd.DataFrame:
    """Per day and release minute: volume ratio to the 10 minutes before, and whether it is a spike."""
    from research.market_data import load_bars
    b = load_bars(ES_BARS, ES_SYMBOLOGY)
    t = b["ts"].dt.tz_convert("America/New_York")
    v = b.groupby([t.dt.date.astype(str), t.dt.strftime("%H:%M")])["volume"].sum()
    rows = []
    for day, g in v.groupby(level=0):
        g = g.droplevel(0)
        for hm in J["news_minutes"]:
            if hm not in g.index:
                continue
            h, m = map(int, hm.split(":"))
            prev = [f"{(h * 60 + m - k) // 60:02d}:{(h * 60 + m - k) % 60:02d}"
                    for k in range(1, J["news_spike_baseline_min"] + 1)]
            base = g.reindex(prev).median()
            ratio = g[hm] / base if base and base > 0 else np.nan
            rows.append(dict(day=day, minute=hm, ratio=ratio, spike=bool(ratio >= J["news_spike_ratio"])))
    return pd.DataFrame(rows)


def load_journal() -> pd.DataFrame:
    t = pd.read_csv(OUT / "journal_trades.csv")
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    t = t[t["closed"]].copy()
    local = t["entry_time"].dt.tz_convert("America/New_York")
    t["day"] = local.dt.date.astype(str)
    t["minute_of_day"] = local.dt.hour * 60 + local.dt.minute + local.dt.second / 60
    return t.sort_values("entry_time").reset_index(drop=True)


def minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def keep_by_day(t: pd.DataFrame, fn) -> np.ndarray:
    """Apply a per-day rule fn(day_trades) -> bool mask, trades in entry order."""
    keep = pd.Series(False, index=t.index)
    for _, g in t.groupby("day", sort=False):
        keep.loc[g.index] = fn(g)
    return keep.to_numpy()


def max_trades(n):
    return lambda g: np.arange(len(g)) < n


def stop_after_losses(k):
    def fn(g):
        losses_before = np.concatenate([[0], np.cumsum(g["net_usd"].to_numpy() < 0)[:-1]])
        return losses_before < k
    return fn


def loss_limit(usd):
    def fn(g):
        before = np.concatenate([[0.0], np.cumsum(g["net_usd"].to_numpy())[:-1]])
        hit = np.minimum.accumulate(before) <= -usd
        return ~hit
    return fn


def profit_stop(usd):
    def fn(g):
        before = np.concatenate([[0.0], np.cumsum(g["net_usd"].to_numpy())[:-1]])
        return ~(np.maximum.accumulate(before) >= usd)
    return fn


def metrics(t: pd.DataFrame, keep: np.ndarray) -> dict:
    k = t[keep]
    net = k["net_usd"]
    daily = k.groupby("day")["net_usd"].sum()
    eq = np.concatenate([[0.0], net.cumsum().to_numpy()])
    return dict(trades=len(k), days=k["day"].nunique(), win_rate=(net > 0).mean() if len(k) else np.nan,
                net_usd=net.sum(), usd_per_trade=net.mean() if len(k) else np.nan,
                profit_factor=net[net > 0].sum() / -net[net < 0].sum() if (net < 0).any() else np.nan,
                max_drawdown=float((np.maximum.accumulate(eq) - eq).max()),
                worst_day=daily.min() if len(daily) else np.nan,
                winning_days=(daily > 0).mean() if len(daily) else np.nan)


def inside_opening_range(t: pd.DataFrame) -> np.ndarray:
    """Entry inside the 09:30-09:45 range of that ES contract (journal section 10)."""
    from research.journal_periods import opening_range
    es = "ES" + t["contract"].str.extract(r"([HMUZ])\d(\d)$").agg("".join, axis=1)
    o = opening_range()
    key = pd.MultiIndex.from_arrays([t["day"], es])
    orh, orl = o["orh"].reindex(key).to_numpy(), o["orl"].reindex(key).to_numpy()
    return (t["entry"].to_numpy() <= orh) & (t["entry"].to_numpy() >= orl)


def evaluate(t: pd.DataFrame, spikes: pd.DataFrame, split: str) -> pd.DataFrame:
    """Every rule on this set of trades (index 0..n-1, entry order), with half-by-half changes."""
    sp = spikes[spikes["spike"]]
    after = {}
    for m_after in J["skip_after_news_minutes"]:
        windows = {}
        for r in sp.itertuples():
            windows.setdefault(r.day, []).append((minutes(r.minute), minutes(r.minute) + m_after))
        after[m_after] = np.array([any(a <= x < b for a, b in windows.get(d, []))
                                   for d, x in zip(t["day"], t["minute_of_day"])])
    open_m = minutes(S["market"]["rth_start_et"])
    inside = inside_opening_range(t)

    rules = {"as traded": np.ones(len(t), bool)}
    for n in J["max_trades"]:
        rules[f"at most {n} trades a day"] = keep_by_day(t, max_trades(n))
    for k in J["stop_after_losses"]:
        rules[f"stop after {k} losing trade{'s' if k > 1 else ''}"] = keep_by_day(t, stop_after_losses(k))
    for usd in J["daily_loss_limit_usd"]:
        rules[f"daily loss limit ${usd}"] = keep_by_day(t, loss_limit(usd))
    for usd in J["daily_profit_stop_usd"]:
        rules[f"stop once up ${usd} on the day"] = keep_by_day(t, profit_stop(usd))
    for m in J["skip_first_minutes"]:
        rules[f"no entries in the first {m} min"] = (t["minute_of_day"] >= open_m + m).to_numpy()
    for m in J["skip_after_news_minutes"]:
        rules[f"no entries {m} min after a news spike"] = ~after[m]
    rules["mornings only (09:30-11:00)"] = ((t["minute_of_day"] >= open_m)
                                           & (t["minute_of_day"] < minutes(J["morning_end_et"]))).to_numpy()
    rules["no entries inside the 09:30-09:45 range"] = ~inside
    rules["no entries inside the 09:30-09:45 range + 10 min after news"] = ~inside & ~after[10]
    base = keep_by_day(t, lambda g: (np.arange(len(g)) < 3) & stop_after_losses(2)(g))
    rules["combo: at most 3 a day + stop after 2 losses"] = base
    rules["combo: + no entries 10 min after news, none in first 5 min"] = (
        base & ~after[10] & (t["minute_of_day"] >= open_m + 5).to_numpy())

    # rules that depend on order (counts, losses, limits) are re-applied after the time filters for the combo
    filtered = t[~after[10] & (t["minute_of_day"] >= open_m + 5).to_numpy()].copy()
    m2 = keep_by_day(filtered, lambda g: (np.arange(len(g)) < 3) & stop_after_losses(2)(g))
    combo2 = np.zeros(len(t), bool)
    combo2[filtered.index[m2]] = True
    rules["combo: time filters first, then at most 3 + stop after 2 losses"] = combo2

    # the owner's first trades of a day had no edge, later ones did (see the report): what if the
    # first N trades of each day were taken on sim (left out), or at half size?
    n_in_day = t.groupby("day").cumcount().to_numpy() + 1
    rules[f"first {J['observe_first_trades']} trades of the day on sim (left out)"] = n_in_day > J["observe_first_trades"]
    rules[f"first {J['observe_first_trades']} on sim + no entries 10 min after news"] = (
        (n_in_day > J["observe_first_trades"]) & ~after[10])
    weights = {f"first {J['observe_first_trades']} trades of the day at half size":
               np.where(n_in_day <= J["observe_first_trades"], 0.5, 1.0)}

    first, second = (t["day"] < split).to_numpy(), (t["day"] >= split).to_numpy()
    rng = np.random.default_rng(S["level_study"]["random_seed"])
    by_day_all = t.groupby("day")["net_usd"].sum()
    out = []
    for name, keep in rules.items():
        row = dict(rule=name, **metrics(t, keep))
        row["change_usd"] = row["net_usd"] - t["net_usd"].sum()
        row["change_first_half"] = t.loc[keep & first, "net_usd"].sum() - t.loc[first, "net_usd"].sum()
        row["change_second_half"] = t.loc[keep & second, "net_usd"].sum() - t.loc[second, "net_usd"].sum()
        kept_by_day = t[keep].groupby("day")["net_usd"].sum().reindex(by_day_all.index, fill_value=0)
        diff = (kept_by_day - by_day_all).to_numpy()
        boots = [diff[rng.integers(0, len(diff), len(diff))].sum() for _ in range(S["level_study"]["bootstrap_samples"])]
        row["change_low"], row["change_high"] = np.percentile(boots, [2.5, 97.5])
        out.append(row)
    for name, w in weights.items():                     # same trades, scaled results
        tw = t.assign(net_usd=t["net_usd"] * w)
        row = dict(rule=name, **metrics(tw, np.ones(len(t), bool)))
        row["change_usd"] = row["net_usd"] - t["net_usd"].sum()
        row["change_first_half"] = tw.loc[first, "net_usd"].sum() - t.loc[first, "net_usd"].sum()
        row["change_second_half"] = tw.loc[second, "net_usd"].sum() - t.loc[second, "net_usd"].sum()
        out.append(row)
    return pd.DataFrame(out)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="",
                    help="only trades from this day on (e.g. the reference period, [journal_rules] reference_start)")
    args = ap.parse_args()
    t = load_journal()
    spikes = news_spikes()
    spikes.to_csv(OUT / "news_spikes_es.csv", index=False)
    if args.start:
        t = t[t["day"] >= args.start].reset_index(drop=True)
        days = sorted(t["day"].unique())
        split = days[len(days) // 2]                   # halves of the period by trading days
        name = "journal_day_rules_from_" + args.start + ".csv"
    else:
        split, name = J["split_date"], "journal_day_rules.csv"
    res = evaluate(t, spikes, split)
    res.to_csv(OUT / name, index=False)
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 30)
    sp = spikes[spikes["spike"]]
    print(f"news spikes found: {sp.groupby('minute').size().to_dict()} (of {spikes['day'].nunique()} days)")
    print(f"{len(t)} trades, {t['day'].nunique()} days, halves split at {split}")
    print(res.round(2).to_string())


if __name__ == "__main__":
    main()
