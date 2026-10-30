"""Level study, build plan step 1.

Questions (STRATEGY.md build plan):
  1. Does price stall at POC clusters more often than at other prices?
  2. Does price cross low-volume areas faster than other prices?
  3. Which long-term window (1, 3 or 6 months) works best?

Run from the repo root:   python -m research.level_study
Needs the files from research.download_level_study_data in data/raw/.
Writes compact results to research/results/level_study/ (committed) and the
large per-event tables to data/derived/ (not committed).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from research.crossings import band_relative_volume, band_visits
from research.databento_http import REPO_ROOT
from research.levels import cluster_pocs
from research.market_data import NY, build_sessions, epoch_seconds, load_bars
from research.paths import bar_path
from research.profiles import ProfileMatrix, bar_volume_at_price, poc, value_area
from research.settings import load_settings, ticks
from research.touches import touch_events

RAW = REPO_ROOT / "data" / "raw"
DERIVED = REPO_ROOT / "data" / "derived"
OUT = REPO_ROOT / "research" / "results" / "level_study"
BARS_FILE = RAW / "MES_FUT_ohlcv-1m_2025-03-01_2026-10-24.dbn.zst"
SYMBOLOGY_FILE = RAW / "MES_FUT_symbology.json"
CONDITION_FILE = RAW / "GLBX_condition.json"


# ---------------------------------------------------------------- inputs

def degraded_dates(start: str, end: str, cache: Path = CONDITION_FILE) -> list[str]:
    """Days Databento marks as reduced quality (free lookup, cached)."""
    if not cache.exists():
        import requests
        resp = requests.get("https://hist.databento.com/v0/metadata.get_dataset_condition",
                            params=dict(dataset="GLBX.MDP3", start_date=start, end_date=end), timeout=60)
        resp.raise_for_status()
        cache.write_text(resp.text)
    rows = json.loads(cache.read_text())
    return [r["date"] for r in rows if r["condition"] != "available"]


def prepare(settings: dict, bars_file: Path = BARS_FILE, symbology_file: Path = SYMBOLOGY_FILE,
            period: tuple[str, str] | None = None, condition_cache: Path = CONDITION_FILE):
    """Front-contract bars, session table, and daily profiles on one price scale.

    Defaults to the level study's MES bars; pass other files (e.g. ES bars for
    the journal) with their period and a separate condition cache.
    """
    ls = settings["level_study"]
    bars = load_bars(bars_file, symbology_file, settings)
    start, end = period or (ls["data_start"], ls["data_end"])
    fb, sessions = build_sessions(bars, degraded_dates(start, end, condition_cache), settings)

    pm = ProfileMatrix(list(sessions.index), fb["low_adj"].min(), fb["high_adj"].max())
    pocs, vals, vahs = [], [], []
    for sess, day in fb.groupby("session", sort=True):
        tk, vol = bar_volume_at_price(day["low_adj"].values, day["high_adj"].values, day["volume"].values)
        pm.add(sess, tk, vol)
        pocs.append(poc(tk, vol))
        lo, hi = value_area(tk, vol, settings["bot"]["value_area_share"])
        vals.append(lo)
        vahs.append(hi)
    tick = settings["market"]["tick_size"]
    sessions["poc_adj"] = pocs
    sessions["val_adj"] = vals
    sessions["vah_adj"] = vahs
    for col in ("poc", "val", "vah"):   # prices in the session's own contract
        sessions[col] = (sessions[f"{col}_adj"] - sessions["offset_ticks"]) * tick
    return fb, sessions, pm


# ---------------------------------------------------------------- per session

def study_sessions(sessions: pd.DataFrame, settings: dict) -> pd.DataFrame:
    """Sessions to score: usable, good data, with a full POC lookback."""
    ls = settings["level_study"]
    lookback = settings["bot"]["poc_lookback_sessions"]
    usable = sessions.index[sessions["use"]]
    rank = pd.Series(np.arange(len(usable)), index=usable)
    keep = sessions["use"] & ~sessions["degraded"] & (sessions.index >= pd.Timestamp(ls["early_check_start"]))
    out = sessions[keep].copy()
    out["prior_usable"] = rank.reindex(out.index).values
    out = out[out["prior_usable"] >= lookback]
    out["period"] = np.where(out.index >= pd.Timestamp(ls["study_start"]), "main", "early")
    max_window = max(ls["long_term_windows_months"]) * ls["sessions_per_month"]
    first_main = out[out["period"] == "main"]["prior_usable"].min()
    if first_main < max_window:
        raise ValueError(f"study_start has only {first_main} prior sessions; need {max_window}")
    return out


def save_daily_profiles(sessions: pd.DataFrame, pm: ProfileMatrix, settings: dict, path: Path) -> None:
    """Volume at each price for every session, in the session's own contract prices."""
    tick = settings["market"]["tick_size"]
    parts = []
    for sess in sessions.index:
        tk, vol = pm.profile(sess)
        raw = tk - sessions.loc[sess, "offset_ticks"]
        parts.append(pd.DataFrame({"session": sess, "contract": sessions.loc[sess, "contract"],
                                   "price": raw * tick, "volume": vol.round(1).astype("float32")}))
    pd.concat(parts, ignore_index=True).to_parquet(path, index=False)


def run(settings: dict | None = None, progress: bool = True):
    s = settings or load_settings()
    ls, bot = s["level_study"], s["bot"]
    fb, sessions, pm = prepare(s)
    todo = study_sessions(sessions, s)

    arm = ticks(ls["arm_distance_points"], s)
    brk = ticks(ls["break_distance_points"], s)
    horizon = ls["outcome_horizon_minutes"] * 60.0
    widths = ls["cluster_widths_ticks"]
    excl = ls["control_min_distance_from_poc_ticks"]
    neighbourhood = ticks(ls["relative_volume_neighborhood_points"], s)
    windows = {m: m * ls["sessions_per_month"] for m in ls["long_term_windows_months"]}

    usable = list(sessions.index[sessions["use"]])
    usable_rows = np.array([pm.row[x] for x in usable])
    ts_s = epoch_seconds(fb["ts"])
    by_session = fb.groupby("session", sort=True).indices

    all_events, all_visits, all_levels = [], [], []
    t0 = time.time()
    for n_done, (sess, info) in enumerate(todo.iterrows()):
        k = int(info["prior_usable"])
        prior = usable[k - bot["poc_lookback_sessions"]:k]
        prior_pocs = sessions.loc[prior, "poc_adj"].values

        rows = by_session[sess]
        day = fb.iloc[rows]
        p, t = bar_path(day["open_adj"].values, day["high_adj"].values, day["low_adj"].values,
                        day["close_adj"].values, ts_s[rows])

        # Question 1: every price in the session's range gets scored.
        prices = np.arange(p.min(), p.max() + 1)
        ev = touch_events(p, t, prices, arm=arm, brk=brk, horizon=horizon,
                          stall=ticks(ls["stall_distance_points"], s))
        dist = np.abs(ev["price"].values[:, None] - prior_pocs[None, :]).min(axis=1)
        ev["poc_distance"] = dist
        ev["control"] = dist >= excl
        for w in widths:
            centers = {}
            for g in cluster_pocs(prior_pocs, w):
                centers[g.price] = max(centers.get(g.price, 0), g.count)
                all_levels.append(dict(session=sess, width=w, price_adj=g.price, count=g.count,
                                       low_adj=g.low, high_adj=g.high))
            ev[f"pocs_w{w}"] = ev["price"].map(centers).fillna(0).astype(int)
        ev["session"] = sess
        ev["period"] = info["period"]
        all_events.append(ev)

        # Questions 2 and 3: zone crossings, rated by each long-term window.
        if info["period"] == "main":
            lts = {months: pm.sum_rows(usable_rows[k - m:k]) for months, m in windows.items()}
            for bw in ls["band_widths_ticks"]:
                v = band_visits(p, t, bw)
                nb = int(round(neighbourhood / bw))
                for months, lt in lts.items():
                    bands, rel = band_relative_volume(lt, pm.tick_lo, bw, nb)
                    v[f"rv_{months}m"] = pd.Series(rel, index=bands).reindex(v["band"].values).values
                v["zone_ticks"] = bw
                v["session"] = sess
                all_visits.append(v)

        if progress and (n_done + 1) % 50 == 0:
            print(f"  {n_done + 1}/{len(todo)} sessions, {time.time() - t0:.0f}s")

    events = pd.concat(all_events, ignore_index=True)
    visits = pd.concat(all_visits, ignore_index=True)
    levels = pd.DataFrame(all_levels)
    rth_start, rth_end = (_minutes(s["market"][k]) for k in ("rth_start_et", "rth_end_et"))
    for df in (events, visits):
        et = pd.to_datetime(df["time" if "time" in df else "enter_time"], unit="s", utc=True).dt.tz_convert(NY)
        mins = et.dt.hour * 60 + et.dt.minute
        df["et_hour"] = et.dt.hour
        df["rth"] = (mins >= rth_start) & (mins < rth_end)
    return fb, sessions, pm, todo, events, visits, levels


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# ---------------------------------------------------------------- statistics

def _boot_ratio(num: np.ndarray, den: np.ndarray, samples: int, rng) -> tuple[float, float]:
    """95% range of sum(num)/sum(den) when whole sessions are resampled."""
    n = len(num)
    if n == 0:
        return np.nan, np.nan
    idx = rng.integers(0, n, size=(samples, n))
    est = num[idx].sum(axis=1) / np.maximum(den[idx].sum(axis=1), 1e-12)
    return tuple(np.percentile(est, [2.5, 97.5]))


def matched_control_expectation(ev: pd.DataFrame, hold_col: str, window_s: float,
                                min_matches: int = 5) -> pd.Series:
    """For each non-control test: hold rate of other-price tests in the same session within +/- window/2.

    Only decided tests count (held or broke). NaN where fewer than `min_matches`.
    """
    out = pd.Series(np.nan, index=ev.index)
    dec = ev[ev[hold_col].notna()]
    for _, g in dec.groupby("session", sort=False):
        ctrl = g[g["control"]].sort_values("time")
        tgt = g[~g["control"]]
        if len(ctrl) == 0 or len(tgt) == 0:
            continue
        ct = ctrl["time"].values
        ch = np.concatenate([[0.0], np.cumsum(ctrl[hold_col].values.astype(float))])
        lo = np.searchsorted(ct, tgt["time"].values - window_s / 2, "left")
        hi = np.searchsorted(ct, tgt["time"].values + window_s / 2, "right")
        cnt = hi - lo
        rate = np.where(cnt >= min_matches, (ch[hi] - ch[lo]) / np.maximum(cnt, 1), np.nan)
        out.loc[tgt.index] = rate
    return out


def classify(ev: pd.DataFrame, width: int) -> pd.Series:
    c = ev[f"pocs_w{width}"]
    return pd.Series(np.select([ev["control"], c >= 3, c == 2, c == 1],
                               ["other prices", "3+ POCs", "2 POCs", "1 POC"], "near a POC"),
                     index=ev.index)


def q1_tables(events: pd.DataFrame, settings: dict):
    ls = settings["level_study"]
    rng = np.random.default_rng(ls["random_seed"])
    B = ls["bootstrap_samples"]
    window_s = ls["time_match_window_minutes"] * 60.0
    rows, split_rows = [], []
    for R in ls["hold_distances_points"]:
        r = ticks(R, settings)
        hold_col = f"hold_{R}"
        decided = (events["mfe"] >= r) | events["broke"]
        events[hold_col] = np.where(decided, (events["mfe"] >= r).astype(float), np.nan)
        exp_col = f"exp_{R}"
        events[exp_col] = matched_control_expectation(events, hold_col, window_s)

    for period in ("main", "early"):
        evp = events[events["period"] == period]
        for w in ls["cluster_widths_ticks"]:
            cls = classify(evp, w)
            for R in ls["hold_distances_points"]:
                hold_col, exp_col = f"hold_{R}", f"exp_{R}"
                for name in ["other prices", "1 POC", "2 POCs", "3+ POCs", "2+ POCs"]:
                    m = (cls.isin(["2 POCs", "3+ POCs"]) if name == "2+ POCs" else cls == name)
                    g = evp[m & evp[hold_col].notna()]
                    rows.append(_q1_row(g, hold_col, exp_col, B, rng,
                                        dict(period=period, width_ticks=w, hold_points=R, group=name)))
    table = pd.DataFrame(rows)

    # Splits for the headline settings
    R = ls["headline_hold_distance_points"]
    hold_col, exp_col = f"hold_{R}", f"exp_{R}"
    evp = events[events["period"] == "main"]
    for w in ls["cluster_widths_ticks"]:
        cls = classify(evp, w)
        lvl = evp[cls.isin(["2 POCs", "3+ POCs"]) & evp[hold_col].notna()]
        splits = {
            "first test of the session": lvl["test_no"] == 1,
            "later tests": lvl["test_no"] > 1,
            "support (price came down to it)": lvl["side"] == 1,
            "resistance (price came up to it)": lvl["side"] == -1,
            "regular hours 09:30-16:00": lvl["rth"],
            "overnight / outside regular hours": ~lvl["rth"],
        }
        for name, m in splits.items():
            split_rows.append(_q1_row(lvl[m], hold_col, exp_col, B, rng,
                                      dict(width_ticks=w, hold_points=R, split=name)))
    return table, pd.DataFrame(split_rows)


def _q1_row(g: pd.DataFrame, hold_col: str, exp_col: str, B: int, rng, base: dict) -> dict:
    row = dict(base)
    row["tests"] = len(g)
    row["sessions"] = g["session"].nunique()
    row["hold_rate"] = g[hold_col].mean() if len(g) else np.nan
    gm = g[g[exp_col].notna()] if exp_col in g else g.iloc[0:0]
    row["tests_matched"] = len(gm)
    if len(gm) and base.get("group") != "other prices":
        per = gm.groupby("session").agg(h=(hold_col, "sum"), e=(exp_col, "sum"), n=(hold_col, "size"))
        row["same_hour_other_prices_rate"] = per["e"].sum() / per["n"].sum()
        row["edge"] = (per["h"].sum() - per["e"].sum()) / per["n"].sum()
        row["edge_low"], row["edge_high"] = _boot_ratio((per["h"] - per["e"]).values, per["n"].values, B, rng)
        row["hold_low"], row["hold_high"] = _boot_ratio(per["h"].values, per["n"].values, B, rng)
    return row


def matched_rank(ev: pd.DataFrame, col: str, window_s: float, min_matches: int = 5) -> pd.Series:
    """For each non-control test: share of same-hour other-price tests with a smaller value.

    0.5 = typical for that hour; above 0.5 = larger than other prices. NaN
    values (price never left before the horizon or session end) count as largest.
    """
    out = pd.Series(np.nan, index=ev.index)
    vals = ev[col].fillna(np.inf)
    for _, g in ev.groupby("session", sort=False):
        ctrl = g[g["control"]].sort_values("time")
        tgt = g[~g["control"]]
        if len(ctrl) < min_matches or len(tgt) == 0:
            continue
        ct, cv = ctrl["time"].values, vals.loc[ctrl.index].values
        lo = np.searchsorted(ct, tgt["time"].values - window_s / 2, "left")
        hi = np.searchsorted(ct, tgt["time"].values + window_s / 2, "right")
        x = vals.loc[tgt.index].values
        ranks = [((cv[a:b] < xi).sum() + 0.5 * (cv[a:b] == xi).sum()) / (b - a) if b - a >= min_matches
                 else np.nan for a, b, xi in zip(lo, hi, x)]
        out.loc[tgt.index] = ranks
    return out


def pause_table(events: pd.DataFrame, settings: dict) -> pd.DataFrame:
    """Do prices near POC levels make price pause longer than other prices tested that hour?"""
    ls = settings["level_study"]
    rng = np.random.default_rng(ls["random_seed"])
    window_s = ls["time_match_window_minutes"] * 60.0
    ev = events[events["period"] == "main"].copy()
    ev["stall_rank"] = matched_rank(ev, "stall_after", window_s)
    rows = []
    for w in ls["cluster_widths_ticks"]:
        cls = classify(ev, w)
        for name in ["other prices", "1 POC", "2 POCs", "3+ POCs", "2+ POCs"]:
            m = cls.isin(["2 POCs", "3+ POCs"]) if name == "2+ POCs" else cls == name
            g = ev[m]
            row = dict(width_ticks=w, group=name, tests=len(g),
                       median_pause_minutes=g["stall_after"].fillna(np.inf).median() / 60)
            if name != "other prices":
                gm = g[g["stall_rank"].notna()]
                if gm.empty:
                    rows.append(row)
                    continue
                per = gm.groupby("session")["stall_rank"].agg(["sum", "size"])
                row["rank_vs_same_hour"] = per["sum"].sum() / per["size"].sum()
                row["rank_low"], row["rank_high"] = _boot_ratio(per["sum"].values, per["size"].values,
                                                                ls["bootstrap_samples"], rng)
            rows.append(row)
    return pd.DataFrame(rows)


def q2_all_widths(visits: pd.DataFrame, settings: dict):
    """q2_tables for each zone width, stacked, with a zone_points column."""
    tick = settings["market"]["tick_size"]
    parts = [[], [], []]
    for bw, g in visits.groupby("zone_ticks"):
        for i, table in enumerate(q2_tables(g, settings)[:3]):
            parts[i].append(table.assign(zone_points=bw * tick))
    return [pd.concat(x, ignore_index=True) for x in parts]


def q2_tables(visits: pd.DataFrame, settings: dict):
    ls = settings["level_study"]
    rng = np.random.default_rng(ls["random_seed"])
    B = ls["bootstrap_samples"]
    v = visits.copy()
    cr = v[v["crossed"]]
    med = cr.groupby(["session", "et_hour"])["seconds"].median().rename("hour_median")
    v = v.join(med, on=["session", "et_hour"])
    v["speed_index"] = v["seconds"] / v["hour_median"]   # 1 = typical crossing time that hour
    v["log_secs"] = np.log1p(v["seconds"])

    rows, cmp_rows, edges_out = [], [], []
    for months in ls["long_term_windows_months"]:
        col = f"rv_{months}m"
        has = v[col] > 0
        edges = np.quantile(v.loc[has, col], [0.2, 0.4, 0.6, 0.8])
        edges_out.append(dict(window_months=months, **{f"edge_{i + 1}": e for i, e in enumerate(edges)}))
        label = np.where(~has, "0 no volume yet", "")
        q = np.digitize(v[col].fillna(0).values, edges) + 1
        names = {1: "1 lowest volume", 2: "2", 3: "3", 4: "4", 5: "5 highest volume"}
        v[f"class_{months}m"] = np.where(has, pd.Series(q).map(names).values, label)
        for name, g in v.groupby(f"class_{months}m"):
            c = g[g["crossed"]]
            rows.append(dict(window_months=months, volume_class=name, visits=len(g),
                             crossed_rate=g["crossed"].mean(),
                             median_minutes_to_cross=c["seconds"].median() / 60,
                             typical_minutes_to_cross=np.expm1(c["log_secs"].mean()) / 60,
                             median_speed_index=c["speed_index"].median()))
        # Lowest vs highest fifth, resampling whole sessions
        lo = v[v[f"class_{months}m"] == "1 lowest volume"]
        hi = v[v[f"class_{months}m"] == "5 highest volume"]
        sess = sorted(set(lo["session"]) & set(hi["session"]))
        a = lo.groupby("session").agg(x=("crossed", "sum"), n=("crossed", "size")).reindex(sess)
        b = hi.groupby("session").agg(x=("crossed", "sum"), n=("crossed", "size")).reindex(sess)
        la = lo[lo["crossed"]].groupby("session")["log_secs"].agg(["sum", "size"]).reindex(sess, fill_value=0)
        lb = hi[hi["crossed"]].groupby("session")["log_secs"].agg(["sum", "size"]).reindex(sess, fill_value=0)
        idx = rng.integers(0, len(sess), size=(B, len(sess)))
        diff = (a["x"].values[idx].sum(1) / a["n"].values[idx].sum(1)
                - b["x"].values[idx].sum(1) / b["n"].values[idx].sum(1))
        ratio = np.exp(la["sum"].values[idx].sum(1) / la["size"].values[idx].sum(1)
                       - lb["sum"].values[idx].sum(1) / lb["size"].values[idx].sum(1))
        point_diff = a["x"].sum() / a["n"].sum() - b["x"].sum() / b["n"].sum()
        point_ratio = np.exp(la["sum"].sum() / la["size"].sum() - lb["sum"].sum() / lb["size"].sum())
        cmp_rows.append(dict(window_months=months, sessions=len(sess),
                             crossed_rate_low=a["x"].sum() / a["n"].sum(),
                             crossed_rate_high=b["x"].sum() / b["n"].sum(),
                             crossed_rate_diff=point_diff,
                             diff_low=np.percentile(diff, 2.5), diff_high=np.percentile(diff, 97.5),
                             time_ratio_low_vs_high=point_ratio,
                             ratio_low=np.percentile(ratio, 2.5), ratio_high=np.percentile(ratio, 97.5)))
    return pd.DataFrame(rows), pd.DataFrame(cmp_rows), pd.DataFrame(edges_out), v


def q2_detail(visits: pd.DataFrame, settings: dict) -> pd.DataFrame:
    """Crossings by finer volume bands, raw and compared with the same day and the same hour.

    "vs same day": difference from the session's average for all zones.
    "vs same hour": difference from the session-hour's average (needs 20+ visits that hour).
    Ranges are 95% session-resampling ranges.
    """
    ls = settings["level_study"]
    rng = np.random.default_rng(ls["random_seed"])
    B = ls["bootstrap_samples"]
    v = visits[visits["zone_ticks"] == ls["headline_band_width_ticks"]].copy()
    v["log_secs"] = np.log1p(v["seconds"])
    for name, keys in (("day", ["session"]), ("hour", ["session", "et_hour"])):
        v[f"cr_{name}"] = v.groupby(keys)["crossed"].transform("mean")
        v[f"n_{name}"] = v.groupby(keys)["crossed"].transform("size")
        v[f"ls_{name}"] = v.join(v[v["crossed"]].groupby(keys)["log_secs"].mean().rename("m"), on=keys)["m"]
    edges = [0.0] + list(ls["relative_volume_bins"])
    labels = (["no volume yet", f"under {edges[1]:g}x"]
              + [f"{a:g}-{b:g}x" for a, b in zip(edges[1:-1], edges[2:])]
              + [f"over {edges[-1]:g}x"])
    cuts = [-np.inf, 1e-9] + edges[1:] + [np.inf]

    def boot(g, base):
        per = g.groupby("session").agg(x=("crossed", "sum"), e=(f"cr_{base}", "sum"), n=("crossed", "size"))
        c = g[g["crossed"]]
        pc = c.groupby("session").agg(d=("log_secs", "sum"), e=(f"ls_{base}", "sum"),
                                      n=("log_secs", "size")).reindex(per.index, fill_value=0)
        idx = rng.integers(0, len(per), size=(B, len(per)))
        ex = (per["x"].values[idx].sum(1) - per["e"].values[idx].sum(1)) / per["n"].values[idx].sum(1)
        tr = np.exp((pc["d"].values[idx].sum(1) - pc["e"].values[idx].sum(1))
                    / np.maximum(pc["n"].values[idx].sum(1), 1))
        return dict(
            **{f"through_vs_{base}_pts": 100 * (per["x"].sum() - per["e"].sum()) / per["n"].sum(),
               f"through_vs_{base}_low": 100 * np.percentile(ex, 2.5),
               f"through_vs_{base}_high": 100 * np.percentile(ex, 97.5),
               f"time_vs_{base}": np.exp((pc["d"].sum() - pc["e"].sum()) / pc["n"].sum()),
               f"time_vs_{base}_low": np.percentile(tr, 2.5),
               f"time_vs_{base}_high": np.percentile(tr, 97.5)})

    rows = []
    for months in ls["long_term_windows_months"]:
        band = pd.cut(v[f"rv_{months}m"].fillna(0), cuts, labels=labels)
        for name, g in v.groupby(band, observed=True):
            row = dict(window_months=months, zone_volume=name, visits=len(g),
                       through_rate=g["crossed"].mean(),
                       typical_seconds_to_cross=np.expm1(g.loc[g["crossed"], "log_secs"].mean()))
            row.update(boot(g, "day"))
            row.update(boot(g[g["n_hour"] >= 20], "hour"))
            rows.append(row)
    return pd.DataFrame(rows)


def hindsight_check(visits: pd.DataFrame, pm: ProfileMatrix, settings: dict) -> pd.DataFrame:
    """Positive control: rate zones by the SAME day's volume (hindsight).

    Price moving fast through a zone is what makes that day's volume there low,
    so this must show an effect. It proves the same-hour comparison can detect
    one of this size.
    """
    ls = settings["level_study"]
    bw = ls["headline_band_width_ticks"]
    nb = int(round(ticks(ls["relative_volume_neighborhood_points"], settings) / bw))
    v = visits[visits["zone_ticks"] == bw].copy()
    parts = []
    for sess, g in v.groupby("session"):
        bands, rel = band_relative_volume(pm.data[pm.row[sess]], pm.tick_lo, bw, nb)
        parts.append(pd.Series(pd.Series(rel, index=bands).reindex(g["band"].values).values, index=g.index))
    v["rv_today"] = pd.concat(parts)
    v["log_secs"] = np.log1p(v["seconds"])
    keys = ["session", "et_hour"]
    v["cr_hour"] = v.groupby(keys)["crossed"].transform("mean")
    v["n_hour"] = v.groupby(keys)["crossed"].transform("size")
    v["ls_hour"] = v.join(v[v["crossed"]].groupby(keys)["log_secs"].mean().rename("m"), on=keys)["m"]
    v = v[v["n_hour"] >= 20]
    band = pd.cut(v["rv_today"].fillna(0), [-np.inf, 0.5, 0.75, 1, 1.25, 1.5, 2, np.inf],
                  labels=["under 0.5x", "0.5-0.75x", "0.75-1x", "1-1.25x", "1.25-1.5x", "1.5-2x", "over 2x"])
    rows = []
    for name, g in v.groupby(band, observed=True):
        c = g[g["crossed"]]
        rows.append(dict(zone_volume_same_day=name, visits=len(g),
                         through_vs_hour_pts=100 * (g["crossed"].mean() - g["cr_hour"].mean()),
                         time_vs_hour=np.exp((c["log_secs"] - c["ls_hour"]).mean())))
    return pd.DataFrame(rows)


def window_pairs(visits: pd.DataFrame, settings: dict) -> pd.DataFrame:
    """Is one long-term window better than another? Same sessions resampled for both."""
    ls = settings["level_study"]
    v = q2_tables(visits[visits["zone_ticks"] == ls["headline_band_width_ticks"]], settings)[3]
    sess = sorted(v["session"].unique())
    rng = np.random.default_rng(ls["random_seed"])
    idx = rng.integers(0, len(sess), size=(ls["bootstrap_samples"], len(sess)))

    def effect(months):
        out = []
        for cls in ("1 lowest volume", "5 highest volume"):
            g = v[v[f"class_{months}m"] == cls]
            a = g.groupby("session")["crossed"].agg(["sum", "size"]).reindex(sess, fill_value=0)
            c = g[g["crossed"]].groupby("session")["log_secs"].agg(["sum", "size"]).reindex(sess, fill_value=0)
            out.append((a["sum"].values[idx].sum(1) / a["size"].values[idx].sum(1),
                        c["sum"].values[idx].sum(1) / c["size"].values[idx].sum(1)))
        (r1, l1), (r5, l5) = out
        return r1 - r5, np.exp(l1 - l5)

    eff = {m: effect(m) for m in ls["long_term_windows_months"]}
    rows = []
    wins = ls["long_term_windows_months"]
    for i, a in enumerate(wins):
        for b in wins[i + 1:]:
            d = eff[a][0] - eff[b][0]
            r = eff[a][1] / eff[b][1]
            rows.append(dict(window_a_months=a, window_b_months=b,
                             extra_crossing_gap=d.mean(), gap_low=np.percentile(d, 2.5),
                             gap_high=np.percentile(d, 97.5), time_ratio_a_vs_b=r.mean(),
                             ratio_low=np.percentile(r, 2.5), ratio_high=np.percentile(r, 97.5)))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- main

def main() -> None:
    s = load_settings()
    t0 = time.time()
    fb, sessions, pm, todo, events, visits, levels = run(s)
    print(f"scored {len(todo)} sessions in {time.time() - t0:.0f}s: "
          f"{len(events):,} price tests, {len(visits):,} zone visits")

    DERIVED.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    events.to_parquet(DERIVED / "level_study_tests.parquet")
    visits.to_parquet(DERIVED / "level_study_zone_visits.parquet")

    # Compact derived data worth keeping in the repo
    tick = s["market"]["tick_size"]
    sess_out = sessions.copy()
    sess_out["roll_gap_points"] = sess_out["roll_gap_ticks"] * tick
    sess_out["offset_points"] = sess_out["offset_ticks"] * tick
    sess_out[["contract", "volume", "poc", "val", "vah", "offset_points", "roll_gap_points",
              "first_bar_et", "last_bar_et", "short", "degraded", "use"]].to_csv(OUT / "sessions.csv")
    save_daily_profiles(sessions, pm, s, OUT / "daily_profiles_1min_bars.parquet")
    levels.assign(price=lambda d: d["price_adj"] * tick).to_csv(OUT / "levels_by_session.csv.gz", index=False)

    q1, q1_splits = q1_tables(events, s)
    q1.to_csv(OUT / "q1_hold_rates.csv", index=False)
    q1_splits.to_csv(OUT / "q1_splits.csv", index=False)
    pause_table(events, s).to_csv(OUT / "q1_pause.csv", index=False)
    q2, q3, edges = q2_all_widths(visits, s)
    q2.to_csv(OUT / "q2_crossing_by_volume.csv", index=False)
    q3.to_csv(OUT / "q3_window_comparison.csv", index=False)
    edges.to_csv(OUT / "q2_volume_class_edges.csv", index=False)
    window_pairs(visits, s).to_csv(OUT / "q3_window_pairs.csv", index=False)
    q2_detail(visits, s).to_csv(OUT / "q2_detail_by_volume.csv", index=False)
    hindsight_check(visits, pm, s).to_csv(OUT / "q2_hindsight_check.csv", index=False)
    todo[["contract", "period"]].to_csv(OUT / "scored_sessions.csv")
    print(f"done in {time.time() - t0:.0f}s; results in {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
