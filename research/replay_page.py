"""Build the zone replay page (an Artifact) from the clips written by research.replay_export.

The clips are shuffled (seeded) so days and markets are mixed. The page records one decision
per clip in its own store (the `db` capability): collection "replay_decisions", document id =
the clip id, with the decision (long / short / skip / unsure), the entry time and price, how
many seconds after the zone test the entry came, and a note. Scored later by
research.replay_score with the owner's risk structure.

Writes research/results/journal/review/replay/index.html; the clips are published next to it
as tests/<id>.json from data/derived/replay/tests/. Run:   python -m research.replay_page
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


def main() -> None:
    s = load_settings()
    tests = pd.read_csv(OUT / "replay_tests.csv")
    tests = tests[[(REPLAY_DIR / f"{i}.json").exists() for i in tests["id"]]]
    order = np.random.default_rng(s["level_study"]["random_seed"]).permutation(len(tests))
    index = [dict(id=r.id, market=r.market) for r in tests.iloc[order].itertuples()]
    out = OUT / "review" / "replay" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text().replace("__INDEX__", json.dumps(index).replace("</", "<\\/")))
    print("wrote", out, len(index), "clips")


if __name__ == "__main__":
    main()
