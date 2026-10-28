"""Download the data the owner approved for checking the journal's timestamps (28 Oct 2026).

Approved: ES 1-minute bars, all contracts, 16 Jan 2024 to 20 Mar 2025, quoted $2.50.
Re-checks the price first, stops if it is above the approved amount, logs the
spend in data-spend.md. Files go to data/raw/ (gitignored).

Run from the repo root:   python -m research.download_journal_bars
"""

from research.databento_http import REPO_ROOT, download, resolve_symbols, save_json

RAW = REPO_ROOT / "data" / "raw"
START, END = "2024-01-16", "2025-03-21"


def main() -> None:
    download(name=f"ES_FUT_ohlcv-1m_{START}_{END}", symbols="ES.FUT", stype_in="parent",
             schema="ohlcv-1m", start=START, end=END, approved_max_cost=2.55, out_dir=RAW,
             description="ES 1-min bars, all contracts (checking journal timestamps)")
    save_json(resolve_symbols("ES.FUT", "parent", "instrument_id", START, END), RAW / "ES_FUT_symbology.json")
    print("saved ES symbology")


if __name__ == "__main__":
    main()
