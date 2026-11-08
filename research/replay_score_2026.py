"""Score the owner's decisions on the 2026 MES replay clips (n001 ...), with the owner's exits.

Clips: research/results/journal/replay_tests_2026.csv (zone tests on 16 MES days of 2026 that pass
the owner's filters). Decisions: the replay page's store (data/derived/replay/decisions/).
  - the owner's entry: the last price when they pressed + 1 tick
  - exits (research.exits): stop-market 4.5, target 10, stop to +1 at +7; also the plain 4.5/10
  - the bot's first 1-point turn and version 4 "sustained volume" (research.entry_read) on the same
    clips, and random entries (20 per clip, random moment in the 5 minutes after the test, random side)
  - decisions whose note says the owner went back after seeing what happened ("go back", "missed",
    "after the fact", "stopped out here") are flagged as hindsight and scored apart
Money: 5 MES. Writes research/results/journal/replay_scores_2026.csv and replay_score_2026_summary.csv.
Run:   python -m research.replay_score_2026
"""

from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd

from research.bot_week_study import FLAT_ET, SLIP_TICKS, et
from research.entry_read import read_sustained
from research.exits import bot_exit
from research.journal_study import OUT
from research.level_test_book import TestReader
from research.replay_score import DECISIONS, TICK, load_day
from research.settings import load_settings

HINDSIGHT = ("go back", "went back", "missed", "after the fact", "stopped out here", "in hindsight")
EXITS = {"4.5/10, stop to +1 at +7": dict(move=True), "4.5/10": dict(move=False)}


def main() -> None:
    s = load_settings()
    rng = np.random.default_rng(s["level_study"]["random_seed"])
    c = s["bot"]["contracts_per_trade"]
    usd = lambda p: p * s["market"]["point_value_usd"] * c - s["backtest"]["commission_per_contract_round_trip"] * c  # noqa: E731
    dec = pd.DataFrame([json.load(open(f)) for f in sorted(glob.glob(str(DECISIONS / "n*.json")))])
    ids = pd.read_csv(OUT / "replay_clip_ids.csv")
    d = dec.merge(ids, left_on="clip_id", right_on="public_id").merge(pd.read_csv(OUT / "replay_tests_2026.csv"), on="id")
    d["decision"] = d["decision"].replace("", np.nan)
    d = d[d["decision"].isin(["long", "short", "skip", "unsure"])]
    rows = []
    for (contract, day), g in d.groupby(["contract", "day"]):
        dt = load_day("MES", contract, day)
        flat = et(day, FLAT_ET)
        for r in g.itertuples():
            zd = int(r.direction)
            far = int(r.lo) if zd == 1 else int(r.hi)
            note = str(r.note or "").lower()
            row = dict(clip=r.clip_id, day=day, decision=r.decision, tf=r.tf, zone_test=r.zone_test,
                       hindsight=any(w in note for w in HINDSIGHT))
            entries = {}
            if r.decision in ("long", "short"):
                od = 1 if r.decision == "long" else -1
                entries["owner"] = (r.entry_time, round(r.entry_price / TICK) + od * SLIP_TICKS, od)
                row["with_zone"] = od == zd
                row["owner_secs_after_test"] = r.entry_time - r.time
            k, _ = TestReader.turn_trade(dt, r.time, zd, int(r.edge))
            if k is not None:
                entries["bot first turn"] = (float(dt.t[k]), int(dt.px[k]) + zd * SLIP_TICKS, zd)
            su = read_sustained(dt, r.time, zd, int(r.edge), far)
            if su:
                entries["bot v4 sustained"] = (su["entry_time"], su["entry_y"] * zd + zd * SLIP_TICKS, zd)
            for who, (t_in, fill, dd) in entries.items():
                for ex, kw in EXITS.items():
                    row[f"{who} | {ex}"] = bot_exit(dt, t_in, fill, dd, flat, **kw)[0] * TICK
            for ex, kw in EXITS.items():
                v = []
                for _ in range(20):
                    i = int(np.searchsorted(dt.t, r.time + rng.uniform(0, 300), "right"))
                    if i < len(dt.t):
                        rd = int(rng.choice([-1, 1]))
                        v.append(bot_exit(dt, float(dt.t[i]), int(dt.px[i]) + rd * SLIP_TICKS, rd, flat, **kw)[0] * TICK)
                row[f"random | {ex}"] = float(np.mean(v)) if v else np.nan
            rows.append(row)
    x = pd.DataFrame(rows)
    x.to_csv(OUT / "replay_scores_2026.csv", index=False)
    took = x["decision"].isin(["long", "short"])
    out = []
    for ex in EXITS:
        def add(name, v):
            v = v.dropna()
            out.append(dict(exit=ex, group=name, trades=len(v), win_rate=(v > 0).mean() if len(v) else np.nan,
                            usd_per_trade=usd(v).mean() if len(v) else np.nan, net_usd=usd(v).sum()))
        add("owner, all takes", x.loc[took, f"owner | {ex}"])
        add("owner, takes without hindsight", x.loc[took & ~x["hindsight"], f"owner | {ex}"])
        add("owner, hindsight takes", x.loc[took & x["hindsight"], f"owner | {ex}"])
        for b in ("bot first turn", "bot v4 sustained"):
            add(f"{b}, clips the owner took", x.loc[took, f"{b} | {ex}"])
            add(f"{b}, clips the owner skipped", x.loc[x["decision"] == "skip", f"{b} | {ex}"])
        add("random, clips the owner took (avg per clip)", x.loc[took, f"random | {ex}"])
        add("random, all clips (avg per clip)", x[f"random | {ex}"])
    t = pd.DataFrame(out)
    t.to_csv(OUT / "replay_score_2026_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(x["decision"].value_counts().to_dict(), "| hindsight takes:", int((took & x["hindsight"]).sum()),
          "| with zone:", x.loc[took, "with_zone"].mean())
    print(t.round(2).to_string())


if __name__ == "__main__":
    main()
