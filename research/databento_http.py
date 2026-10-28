"""Small Databento HTTP client.

The sandbox proxy adds the API key to requests sent to hist.databento.com,
so no credentials appear here.

Cost rules (see CLAUDE.md): every paid download first gets a fresh price
quote (free) and refuses to run if the quote is above the amount the owner
approved. Each paid download is appended to data-spend.md.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import requests

BASE_URL = "https://hist.databento.com/v0"
DATASET = "GLBX.MDP3"
STARTING_CREDIT = 125.00
REPO_ROOT = Path(__file__).resolve().parent.parent
SPEND_LOG = REPO_ROOT / "data-spend.md"


def _get(endpoint: str, params: dict) -> requests.Response:
    resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=60)
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Databento refused the request ({resp.status_code}). "
            "Ask the owner to check the Databento secret in the environment settings."
        )
    resp.raise_for_status()
    return resp


def get_cost(symbols: str, schema: str, start: str, end: str, stype_in: str) -> float:
    """Price of a request in US dollars. This call is free."""
    params = dict(dataset=DATASET, symbols=symbols, schema=schema,
                  start=start, end=end, stype_in=stype_in)
    return float(_get("metadata.get_cost", params).text)


def resolve_symbols(symbols: str, stype_in: str, stype_out: str,
                    start_date: str, end_date: str) -> dict:
    """Symbol lookup (free). Returns Databento's `result` mapping."""
    resp = requests.post(f"{BASE_URL}/symbology.resolve", timeout=60, data=dict(
        dataset=DATASET, symbols=symbols, stype_in=stype_in, stype_out=stype_out,
        start_date=start_date, end_date=end_date))
    resp.raise_for_status()
    body = resp.json()
    if body.get("partial") or body.get("not_found"):
        print("symbology warning:", body.get("message"), body.get("partial"), body.get("not_found"))
    return body["result"]


def spent_so_far() -> float:
    """Sum of the cost column in data-spend.md."""
    if not SPEND_LOG.exists():
        return 0.0
    total = 0.0
    for line in SPEND_LOG.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[4].startswith("$"):
            try:
                total += float(cells[4].lstrip("$"))
            except ValueError:
                pass
    return total


def log_spend(what: str, schema: str, period: str, cost: float) -> None:
    if not SPEND_LOG.exists():
        SPEND_LOG.write_text(
            "# Databento spend log\n\n"
            f"Starting free credit: ${STARTING_CREDIT:.2f} (expires around April 2027).\n"
            "Costs are Databento's quote taken right before each download.\n\n"
            "| Date | Request | Schema | Period | Cost | Est. credit left |\n"
            "|---|---|---|---|---|---|\n"
        )
    left = STARTING_CREDIT - spent_so_far() - cost
    with SPEND_LOG.open("a") as f:
        f.write(f"| {dt.date.today().isoformat()} | {what} | {schema} | {period} "
                f"| ${cost:.2f} | ${left:.2f} |\n")


def download(*, name: str, symbols: str, schema: str, start: str, end: str,
             stype_in: str, approved_max_cost: float, out_dir: Path,
             description: str, log: bool = True) -> Path:
    """Download one request as zstd-compressed DBN, after a fresh price check.

    log=False leaves the spend log to the caller (for batches logged as one line).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.dbn.zst"
    if out_path.exists():
        print(f"{name}: already downloaded, skipping (no charge)")
        return out_path

    cost = get_cost(symbols, schema, start, end, stype_in)
    if cost > approved_max_cost + 1e-9:
        raise RuntimeError(f"{name}: quote ${cost:.2f} is above the approved ${approved_max_cost:.2f}. Not downloading.")
    print(f"{name}: quote ${cost:.4f} (approved up to ${approved_max_cost:.2f}), downloading...")

    params = dict(dataset=DATASET, symbols=symbols, schema=schema, start=start, end=end,
                  stype_in=stype_in, stype_out="instrument_id",
                  encoding="dbn", compression="zstd")
    tmp_path = out_path.with_suffix(".part")
    with requests.post(f"{BASE_URL}/timeseries.get_range", data=params,
                       stream=True, timeout=600) as resp:
        # 206 means Databento is sending data but some symbols only exist for
        # part of the period (normal for a parent symbol like MES.FUT). The
        # data is still good and the request is billed, so always save it.
        if resp.status_code not in (200, 206):
            raise RuntimeError(f"{name}: download failed {resp.status_code}: "
                               f"{resp.raw.read(500, decode_content=True)!r}")
        for key, value in resp.headers.items():
            if "warning" in key.lower():
                print(f"{name}: Databento warning: {value}")
        with tmp_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    tmp_path.rename(out_path)
    if log:
        log_spend(description, schema, f"{start} to {end}", cost)
    print(f"{name}: saved {out_path.stat().st_size / 1e6:.1f} MB (${cost:.4f})")
    return out_path


def save_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True))
