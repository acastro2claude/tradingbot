"""Build the zone replay page (an Artifact) from the clips written by research.replay_export.

The clips are shuffled (seeded) so days and markets are mixed. The page records one decision
per clip in its own store (the `db` capability): collection "replay_decisions", document id =
the clip's public id (c001, c002 ...; research/results/journal/replay_clip_ids.csv maps them
to the test, so no date shows on the page or in file names), with the decision (long / short / skip / unsure), the entry time and price, how
many seconds after the zone test the entry came, and a note. Scored later by
research.replay_score with the owner's risk structure.

Second look: the clips in research/results/journal/replay_recheck.csv (research.replay_invalid
--recheck) come first, as new undecided clips r001 ... (round 2 in replay_clip_ids.csv).

Writes research/results/journal/review/replay/index.html; the clips are published next to it
as tests/<public id>.json from data/derived/replay/public/. Run:   python -m research.replay_page
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.journal_study import OUT
from research.replay_export import REPLAY_DIR
from research.settings import load_settings

TEMPLATE = Path(__file__).with_name("templates") / "zone_replay.html"
PUBLIC_DIR = REPLAY_DIR.parent / "public"          # the clips as published: c001.json ... in play order


def main() -> None:
    s = load_settings()
    tests = pd.read_csv(OUT / "replay_tests.csv")
    tests = tests[[(REPLAY_DIR / f"{i}.json").exists() for i in tests["id"]]]
    order = np.random.default_rng(s["level_study"]["random_seed"]).permutation(len(tests))
    shuffled = tests.iloc[order].reset_index(drop=True)
    shuffled["public_id"] = [f"c{k + 1:03d}" for k in range(len(shuffled))]          # no dates in names
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    for r in shuffled.itertuples():
        clip = json.loads((REPLAY_DIR / f"{r.id}.json").read_text())
        clip["id"] = r.public_id
        (PUBLIC_DIR / f"{r.public_id}.json").write_text(json.dumps(clip, separators=(",", ":")))
    ids = shuffled[["public_id", "id", "market"]].assign(round=1)
    rc = OUT / "replay_recheck.csv"                 # second look (research.replay_invalid --recheck): shown first
    if rc.exists():
        again = pd.read_csv(rc).merge(tests[["id", "market"]], on="id")
        for r in again.itertuples():
            clip = json.loads((REPLAY_DIR / f"{r.id}.json").read_text())
            clip["id"] = r.public_id
            (PUBLIC_DIR / f"{r.public_id}.json").write_text(json.dumps(clip, separators=(",", ":")))
        ids = pd.concat([again[["public_id", "id", "market"]].assign(round=2), ids], ignore_index=True)
    ids[["public_id", "id", "round"]].to_csv(OUT / "replay_clip_ids.csv", index=False)
    index = [dict(id=r.public_id, market=r.market) for r in ids.itertuples()]
    out = OUT / "review" / "replay" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text().replace("__INDEX__", json.dumps(index).replace("</", "<\\/")))
    print("wrote", out, len(index), "clips")


if __name__ == "__main__":
    main()
