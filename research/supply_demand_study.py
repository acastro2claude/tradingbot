"""Supply and demand overlay (STRATEGY.md 1b + 1e + level strength).

The owner's read: a recent uncovered POC is a better level when it lines up
with a supply or demand zone. For each zone timeframe this scores tests of:
  - uncovered recent POCs inside a matching live zone (the confluence)
  - uncovered recent POCs with no matching zone
  - zone near edges with no POC within 2 points (zones on their own)
  - any price inside a matching zone with no POC within 2 points
against other prices (no POC within 2 points, not in any live zone) tested
in the same hour. Matching: support tests (price came down) need a demand
zone, resistance tests a supply zone.

Not tested here: absorption (step 2), targets and the checkpoint.

Run after research.level_study:   python -m research.supply_demand_study
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.level_study import DERIVED, OUT, _q1_row, matched_control_expectation, prepare
from research.recent_poc_study import recent_poc_labels
from research.settings import load_settings, ticks
from research.supply_demand import find_zones, label_tests, make_candles, zone_lifetimes


def main() -> None:
    s = load_settings()
    ls, bot = s["level_study"], s["bot"]
    tick = s["market"]["tick_size"]
    rng = np.random.default_rng(ls["random_seed"])
    B = ls["bootstrap_samples"]
    window_s = ls["time_match_window_minutes"] * 60.0
    far = ls["control_min_distance_from_poc_ticks"]

    fb, sessions, _ = prepare(s)
    ev = pd.read_parquet(DERIVED / "level_study_tests.parquet")
    labels = recent_poc_labels(sessions, pd.Index(ev["session"].unique()), bot["recent_poc_lookback_sessions"])
    lab = (labels.sort_values(["covered", "age"]).drop_duplicates(["session", "price"])
                 .set_index(["session", "price"]))
    ev = ev.join(lab, on=["session", "price"])
    uncovered = ev["covered"].eq(False)
    no_poc = ev["poc_distance"] >= far
    for R in ls["hold_distances_points"]:
        r = ticks(R, s)
        decided = (ev["mfe"] >= r) | ev["broke"]
        ev[f"hold_{R}"] = np.where(decided, (ev["mfe"] >= r).astype(float), np.nan)

    lookback = bot["sd_lookback_months"] * ls["sessions_per_month"]
    rows, stats = [], []
    for tf in ls["sd_timeframes_minutes"]:
        candles = make_candles(fb, tf)
        zones = find_zones(candles, bot["sd_base_min_candles"], bot["sd_departure_min_ratio"],
                           bot["sd_zone_drawing"])
        zones = zone_lifetimes(zones, candles, fb, bot["sd_chop_invalid_minutes"], lookback,
                               list(sessions.index))
        zones.assign(timeframe_minutes=tf, low_price=zones["low"] * tick, high_price=zones["high"] * tick) \
             .to_csv(DERIVED / f"sd_zones_{tf}min.csv", index=False)
        life_h = (zones["dies"].replace(np.inf, np.nan) - zones["created"]) / 3600
        stats.append(dict(timeframe_minutes=tf, zones=len(zones),
                          zones_per_session=len(zones) / sessions["use"].sum(),
                          demand_share=(zones["kind"] == "demand").mean(),
                          median_height_points=((zones["high"] - zones["low"]) * tick).median(),
                          median_life_hours=life_h.median(),
                          **{f"ended_{k.replace(' ', '_')}": v for k, v in
                             zones["why"].value_counts(normalize=True).items()}))

        for cap in [None] + list(ls["sd_height_caps_points"]):
            zz = zones if cap is None else zones[(zones["high"] - zones["low"]) * tick <= cap]
            e = ev.join(label_tests(ev, zz))
            e["control"] = no_poc & ~e["in_any_zone"]
            groups = {
                "POC inside a matching zone (confluence)": uncovered & e["in_zone"],
                "POC, no matching zone": uncovered & ~e["in_zone"],
                "zone near edge, no POC nearby": e["zone_edge"] & no_poc,
                "inside a zone, no POC nearby": e["in_zone"] & no_poc,
                "other prices (no POC, no zone)": e["control"],
            }
            if cap is not None:   # only the zone groups change meaningfully
                groups = {k: v for k, v in groups.items() if "zone near edge" in k or "confluence" in k}
            for R in ls["hold_distances_points"]:
                hold_col, exp_col = f"hold_{R}", f"exp_{R}"
                e[exp_col] = matched_control_expectation(e, hold_col, window_s)
                for period in ("main", "early"):
                    inp = (e["period"] == period) & e[hold_col].notna()
                    for name, m in groups.items():
                        rows.append(_q1_row(e[inp & m], hold_col, exp_col, B, rng,
                                            dict(timeframe_minutes=tf, max_zone_points=cap or "any",
                                                 zones_used=len(zz), period=period, hold_points=R,
                                                 group=name)))
        print(f"{tf}-minute zones done: {len(zones)} zones")

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "sd_overlay_hold_rates.csv", index=False)
    pd.DataFrame(stats).to_csv(OUT / "sd_zone_stats.csv", index=False)
    show = out[(out["period"] == "main") & (out["hold_points"] == ls["headline_hold_distance_points"])]
    print(pd.DataFrame(stats).round(2).to_string(index=False))
    print(show[["timeframe_minutes", "max_zone_points", "group", "tests", "sessions", "hold_rate", "same_hour_other_prices_rate",
                "edge", "edge_low", "edge_high"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
