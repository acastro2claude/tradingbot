"""Level source B (STRATEGY.md 1e): recent uncovered POCs.

The owner's method: trade reversals against the POCs of the last 5 sessions,
dropping any POC that the next session's value area covered.

This scores tests of those POCs exactly like question 1 of the level study
(held = 6 points back before 3 through, compared with other prices tested
in the same hour), and does the same for the covered POCs the owner drops,
as a contrast.

Not tested here: the supply and demand overlay (1b is still TBD), the
targets and the checkpoint.

Run after research.level_study:   python -m research.recent_poc_study
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.level_study import DERIVED, OUT, _q1_row, matched_control_expectation, prepare
from research.settings import load_settings, ticks


def recent_poc_labels(sessions: pd.DataFrame, scored: pd.Index, lookback: int) -> pd.DataFrame:
    """For each scored session, its recent POCs (adjusted ticks), age in sessions, and covered flag."""
    usable = list(sessions.index[sessions["use"]])
    pos = {s: i for i, s in enumerate(usable)}
    rows = []
    for sess in scored:
        k = pos[sess]
        for age in range(1, lookback + 1):
            src = usable[k - age]
            nxt = usable[k - age + 1]           # the session after the POC's session
            poc = sessions.loc[src, "poc_adj"]
            covered = age > 1 and sessions.loc[nxt, "val_adj"] <= poc <= sessions.loc[nxt, "vah_adj"]
            rows.append(dict(session=sess, price=poc, age=age, covered=bool(covered)))
    return pd.DataFrame(rows)


def main() -> None:
    s = load_settings()
    ls = s["level_study"]
    rng = np.random.default_rng(ls["random_seed"])
    B = ls["bootstrap_samples"]
    window_s = ls["time_match_window_minutes"] * 60.0

    _, sessions, _ = prepare(s)
    ev = pd.read_parquet(DERIVED / "level_study_tests.parquet")
    labels = recent_poc_labels(sessions, pd.Index(ev["session"].unique()),
                               s["bot"]["recent_poc_lookback_sessions"])
    # A price counts as uncovered if any recent POC there is uncovered; keep the youngest age.
    lab = (labels.sort_values(["covered", "age"])
                 .drop_duplicates(["session", "price"])
                 .set_index(["session", "price"]))
    ev = ev.join(lab, on=["session", "price"])

    rows = []
    for R in ls["hold_distances_points"]:
        r = ticks(R, s)
        hold_col, exp_col = f"hold_{R}", f"exp_{R}"
        decided = (ev["mfe"] >= r) | ev["broke"]
        ev[hold_col] = np.where(decided, (ev["mfe"] >= r).astype(float), np.nan)
        ev[exp_col] = matched_control_expectation(ev, hold_col, window_s)
        for period in ("main", "early"):
            evp = ev[(ev["period"] == period) & ev[hold_col].notna()]
            groups = {
                "other prices": evp["control"],
                "uncovered recent POC (owner's levels)": evp["covered"].eq(False),
                "  yesterday's POC": evp["covered"].eq(False) & evp["age"].eq(1),
                "  uncovered, 2-5 days old": evp["covered"].eq(False) & evp["age"].gt(1),
                "covered recent POC (dropped)": evp["covered"].eq(True),
            }
            for name, m in groups.items():
                rows.append(_q1_row(evp[m], hold_col, exp_col, B, rng,
                                    dict(period=period, hold_points=R, group=name)))
            # first test of the session at the owner's levels
            m = evp["covered"].eq(False) & evp["test_no"].eq(1)
            rows.append(_q1_row(evp[m], hold_col, exp_col, B, rng,
                                dict(period=period, hold_points=R,
                                     group="  uncovered, first test of the session")))
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "recent_pocs_hold_rates.csv", index=False)
    show = out[out["hold_points"] == ls["headline_hold_distance_points"]]
    print(show[["period", "group", "tests", "sessions", "hold_rate", "same_hour_other_prices_rate",
                "edge", "edge_low", "edge_high"]].round(3).to_string(index=False))
    cov = labels[labels["age"] > 1]["covered"].mean()
    print(f"share of 2-5 day old POCs covered by the next day's value area: {cov:.0%}")


if __name__ == "__main__":
    main()
