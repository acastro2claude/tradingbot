"""Why the owner called most replay zones "invalid": a look at what price did around each zone.

On the zone replay page the owner skipped 77 of 101 clips, most with "invalid zone", "price
already blew past this zone both up and down (previous day)", "demand zone above price",
"price hung around too long in the zone". This measures, for every clip they decided:
  through_after_pts   how far price traded past the far edge between the zone forming and the
                      test (the bot only ends a zone when a candle of its timeframe closes past it)
  minutes_inside      1-minute closes inside the zone between forming and the test
  traded_both_before  in the context chart's sessions before the zone formed, price traded at
                      least 5 points on both sides of the zone (the page drew the zone band
                      across the whole context chart, also before the zone existed)
  formed_overnight    the zone formed between 18:00 and 09:30 ET
  hours_old           hours from the zone forming to the test
and compares the clips the owner called invalid, the other skips, and the trades.

Writes research/results/journal/replay_invalid.csv. Run:   python -m research.replay_invalid
"""

from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd

from research.journal_study import OUT
from research.level_study import DERIVED
from research.market_data import epoch_seconds
from research.settings import load_settings
from research.zone_course_chart import es_zones
from research.zone_study import NAMES

INVALID_WORDS = ("invalid", "not valid", "no longer valid", "blew past", "blown past", "blows past", "below it",
                 "above price", "too long", "hanging", "hung around", "jumped way above", "so far below")


def label(decision: str, note: str) -> str:
    if decision in ("long", "short"):
        return "took the trade"
    if decision == "skip" and any(w in (note or "").lower() for w in INVALID_WORDS):
        return "skipped: invalid zone"
    return "skipped: other reason" if decision == "skip" else "not sure"


def main() -> None:
    s = load_settings()
    fb, sessions, zones, _ = es_zones(s)
    off = sessions.groupby("contract")["offset_ticks"].first()
    t = epoch_seconds(fb["ts"])
    lo, hi, cl = (fb[k].to_numpy() for k in ("low_adj", "high_adj", "close_adj"))
    sess = pd.to_datetime(fb["session"]).to_numpy()
    tick, ctx_n = s["market"]["tick_size"], s["replay"]["context_sessions"]
    all_sess = np.unique(sess)

    dec = pd.DataFrame([json.load(open(f)) for f in sorted(glob.glob(str(DERIVED / "replay/decisions/replay_decisions/*.json")))])
    clips = pd.read_csv(OUT / "replay_clip_ids.csv").merge(pd.read_csv(OUT / "replay_tests.csv"), on="id")
    d = dec.merge(clips, left_on="clip_id", right_on="public_id")
    d = d[d["decision"].isin(["long", "short", "skip", "unsure"])]
    rows = []
    for r in d.itertuples():
        es = r.contract if r.market == "ES" else "ES" + r.contract[3:]
        o, kind = int(off[es]), "demand" if r.direction == 1 else "supply"
        m = zones[(zones["kind"] == kind) & (zones["near"] - o == r.edge) & (zones["created"] <= r.time) & (zones["dies"] > r.time)]
        if m.empty:
            continue
        z = m.sort_values("timeframe").iloc[-1]
        d_ = 1 if kind == "demand" else -1
        far = z.low if d_ == 1 else z.high
        a, b = np.searchsorted(t, z.created, "left"), np.searchsorted(t, r.time - 60, "left")
        through = ((far - lo[a:b].min()) if d_ == 1 else (hi[a:b].max() - far)) * tick if b > a else np.nan
        inside = int(((cl[a:b] >= z.low) & (cl[a:b] <= z.high)).sum()) if b > a else 0
        k = int(np.searchsorted(all_sess, np.datetime64(r.day)))
        first = all_sess[max(0, k - ctx_n)]
        c0, c1 = np.searchsorted(sess, first, "left"), np.searchsorted(t, z.formed, "left")
        both = bool(c1 > c0 and hi[c0:c1].max() >= z.high + 5 / tick and lo[c0:c1].min() <= z.low - 5 / tick)
        hour = pd.Timestamp(z.formed, unit="s", tz="UTC").tz_convert("America/New_York")
        rows.append(dict(clip=r.clip_id, decision=r.decision, group=label(r.decision, r.note), tf=NAMES[z.timeframe],
                         test=r.zone_test, through_after_pts=max(through, 0.0) if through == through else np.nan,
                         minutes_inside=inside, traded_both_before=both,
                         formed_overnight=not (9.5 <= hour.hour + hour.minute / 60 < 18),
                         hours_old=(r.time - z.formed) / 3600, note=r.note))
    x = pd.DataFrame(rows)
    x.to_csv(OUT / "replay_invalid.csv", index=False)
    g = x.groupby("group").agg(clips=("clip", "size"),
                               through_after_any=("through_after_pts", lambda v: (v > 0).mean()),
                               through_after_2pts=("through_after_pts", lambda v: (v >= 2).mean()),
                               minutes_inside_median=("minutes_inside", "median"),
                               inside_30min_plus=("minutes_inside", lambda v: (v >= 30).mean()),
                               traded_both_before=("traded_both_before", "mean"),
                               formed_overnight=("formed_overnight", "mean"),
                               hours_old_median=("hours_old", "median"))
    pd.set_option("display.width", 250)
    print(g.round(2).to_string())


if __name__ == "__main__":
    main()
