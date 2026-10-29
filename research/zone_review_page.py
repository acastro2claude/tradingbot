"""Build the interactive zone review page (an Artifact) for the owner.

All ES candles (2024-01 to 2025-03) on the owner's five timeframes, with the bot's
supply/demand zones (research.zones, course rules) drawn on a zoomable chart. The owner
marks each zone Valid / Noise / Not sure with a note, and writes the zones the bot
missed per week. Marks are saved in the page's own store (the `db` capability):
  zone_verdicts   one document per zone, id "<timeframe minutes>-<formed epoch>-<d|s>"
  missing_zones   one document per week, id = the week's Monday (YYYY-MM-DD)
Each verdict also stores the zone's timeframe, side, prices and formation time, so it
still means something after the zone rules change.

Zones that a higher-timeframe zone later formed over are left out (hindsight view).
Run from the repo root:   python -m research.zone_review_page
Writes research/results/journal/review/zones/index.html.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.journal_study import OUT
from research.settings import load_settings
from research.zone_course_chart import es_zones

TEMPLATE = Path(__file__).with_name("templates") / "zone_review.html"
START_WEEK = "2024-08-12"                  # the owner's 14-15 Aug 2024 example
RULES_VERSION = "course rules, Oct 2026 (opposite basing candle, 1.5x move away, retests)"


def encode(c: pd.DataFrame, session_index: dict) -> dict:
    """Candles as differences: start step, open vs previous close, high/low/close vs open (ticks)."""
    t = c["start"].astype(np.int64).to_numpy()
    o, h, lo, cl = (c[k].astype(np.int64).to_numpy() for k in ("open", "high", "low", "close"))
    prev_c = np.concatenate([[0], cl[:-1]])
    return dict(dt=np.diff(t, prepend=0).tolist(), o=(o - prev_c).tolist(), h=(h - o).tolist(), l=(o - lo).tolist(),
                c=(cl - o).tolist(), s=c["session"].map(session_index).astype(int).tolist())


def weeks(sessions: pd.DataFrame, c15: pd.DataFrame) -> list[dict]:
    span = c15.groupby("session").agg(t0=("start", "min"), t1=("end", "max"))
    out = []
    for monday, g in span.groupby(span.index.to_period("W-SUN").start_time):
        last = g.index.max()
        out.append(dict(monday=monday.strftime("%Y-%m-%d"), label=monday.strftime("%a %d %b %Y"),
                        last=last.strftime("%a %d %b"), t0=float(g["t0"].min()), t1=float(g["t1"].max()),
                        off=int(sessions.loc[last, "offset_ticks"]), contract=str(sessions.loc[last, "contract"])))
    return out


def main() -> None:
    s = load_settings()
    _, sessions, z, candles = es_zones(s)
    tfs = sorted(candles, reverse=True)
    names = list(sessions.index)
    index = {d: i for i, d in enumerate(names)}
    shown = z[z["why"] != "replaced by a higher timeframe"]
    zones = [dict(id=f"{r.timeframe}-{int(r.formed)}-{r.kind[0]}", tf=int(r.timeframe), kind=r.kind, pat=r.pattern,
                  near=int(r.near), lo=int(r.low), hi=int(r.high), base=float(r.base_start), formed=float(r.formed),
                  known=float(r.created), dies=None if not np.isfinite(r.dies) else float(r.dies), why=r.why,
                  tests=[float(x) for x in r.tests],
                  mine=bool(r.timeframe == 240 and r.kind == "demand" and r.created_session == pd.Timestamp("2024-08-15")))
             for r in shown.itertuples()]
    data = dict(tick=s["market"]["tick_size"], weak_from_test=s["zones"]["weak_from_test"], tfs=tfs,
                start_week=START_WEEK, rules_version=RULES_VERSION,
                sessions=[d.strftime("%Y-%m-%d") for d in names],
                candles={tf: encode(candles[tf], index) for tf in tfs},
                weeks=weeks(sessions, candles[min(tfs)]), zones=zones)
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    out = OUT / "review" / "zones" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text().replace("__DATA__", payload))
    print("wrote", out, f"{out.stat().st_size / 1e6:.2f} MB,", len(zones), "zones,", len(data["weeks"]), "weeks")


if __name__ == "__main__":
    main()
