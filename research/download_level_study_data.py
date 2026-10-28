"""Download the data the owner approved for the level study (27 Oct 2026).

Approved:
  - MES 1-minute bars, all contracts, 1 Mar 2025 to 23 Oct 2026: quoted $3.37
  - MES trades for 4 check days: quoted $2.38 in total
      3 Apr 2025 (Example 1, MESM5), 21 Oct 2025 (MESZ5),
      24 Feb 2026 (MESH6), 21 Jul 2026 (MESU6)

Each request re-checks the price first and stops if it is above the
approved amount. Files go to data/raw/ (gitignored).

Run from the repo root:  python -m research.download_level_study_data
"""

from research.databento_http import REPO_ROOT, download, resolve_symbols, save_json

RAW = REPO_ROOT / "data" / "raw"

BARS = dict(
    name="MES_FUT_ohlcv-1m_2025-03-01_2026-10-24",
    symbols="MES.FUT", stype_in="parent", schema="ohlcv-1m",
    start="2025-03-01", end="2026-10-24", approved_max_cost=3.40,
    description="MES 1-min bars, all contracts (level study)",
)

# Full Globex sessions: 18:00 ET the evening before to 17:00 ET (UTC times below).
TRADE_DAYS = [
    ("MESM5", "2025-04-02T22:00", "2025-04-03T21:00", 1.06, "Example 1 day"),
    ("MESZ5", "2025-10-20T22:00", "2025-10-21T21:00", 0.37, "check day"),
    ("MESH6", "2026-02-23T23:00", "2026-02-24T22:00", 0.58, "check day"),
    ("MESU6", "2026-07-20T22:00", "2026-07-21T21:00", 0.40, "check day"),
]


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-bars", action="store_true",
                        help="only download the trade days (the bars need separate approval)")
    args = parser.parse_args()
    if not args.skip_bars:
        download(out_dir=RAW, **BARS)
    for symbol, start, end, max_cost, why in TRADE_DAYS:
        session = end[:10]
        download(name=f"{symbol}_trades_{session}", symbols=symbol, stype_in="raw_symbol",
                 schema="trades", start=start, end=end, approved_max_cost=max_cost,
                 out_dir=RAW, description=f"{symbol} trades, session {session} ({why})")

    # Free: which contract each instrument ID meant on each date (IDs get reused).
    mapping = resolve_symbols("MES.FUT", "parent", "instrument_id", "2025-03-01", "2026-10-24")
    save_json(mapping, RAW / "MES_FUT_symbology.json")
    print("saved symbology for", len(mapping), "MES instruments")


if __name__ == "__main__":
    main()
