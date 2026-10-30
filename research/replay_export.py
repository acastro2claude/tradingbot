"""Export every zone test on the order-book days as a replay clip for the zone replay page.

Days: the 23 ES days of 2024 (data/raw/es_weeks_mbo) and the owner's 20 busiest MES journal
days (data/raw/journal_mbo, which run from midnight UTC to the end of the owner's trading
that day). Zone tests: research.zones zones (from the ES 1-minute bars, the same prices as
MES in the same contract month), tested between 09:30 and 15:30 ET (find_tests: price
comes back to the near edge after being 5 points away).

Each clip ([replay] settings): the book heatmap around the zone edge in 2-second frames from
10 minutes before the test to 5 minutes after, the tape (close/high/low, buy and sell
volume, cumulative delta), the biggest trades as bubbles, absorption bursts like the
owner's add-on, and a 15-minute context chart of the previous sessions with the zone. The
date is not shown on the page.

Writes data/derived/replay/tests/<id>.json (the clips, too big for git), the trades of each
MES day to data/derived/replay_trades/ (for scoring the owner's entries later), and
research/results/journal/replay_tests.csv (the list of tests, committed).
Run from the repo root:   python -m research.replay_export      (--backfill-vwap: add the volume bubbles' prices to old clips)
"""

from __future__ import annotations

import base64
import json
from multiprocessing import Pool

import numpy as np
import pandas as pd

from research.bot_week_study import BOOK_DIR, et
from research.journal_study import OUT
from research.level_study import DERIVED, RAW
from research.level_test_book import day_bursts, find_tests
from research.orderbook import build_book, load_mbo
from research.orderbook_study import typical_depth
from research.settings import load_settings
from research.zone_book_study import zone_levels, zone_of
from research.zone_course_chart import es_zones

REPLAY_DIR = DERIVED / "replay" / "tests"
TRADES_OUT = DERIVED / "replay_trades"
MES_DIR = RAW / "journal_mbo"
CTX = {}


def days() -> list[dict]:
    out = []
    plan = pd.read_csv(OUT / "es_week_price_plan.csv")
    for r in plan.itertuples():
        f = BOOK_DIR / f"{r.es}_{r.day}_mbo.dbn.zst"
        if f.exists():
            out.append(dict(market="ES", contract=r.es, es=r.es, day=r.day, file=str(f), end=et(r.day, "16:00")))
    mp = pd.read_csv(OUT / "orderbook_price_plan.csv")
    for r in mp.itertuples():
        f = MES_DIR / f"{r.mes}_{r.day}_mbo.dbn.zst"
        if f.exists():
            end = min(et(r.day, "16:00"), pd.Timestamp(r.end, tz="UTC").timestamp())
            out.append(dict(market="MES", contract=r.mes, es="ES" + r.mes[3:], day=r.day, file=str(f), end=end))
    return out


def b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def vwap_frames(f: np.ndarray, px: np.ndarray, sz: np.ndarray, n: int) -> list:
    """Volume-weighted price per frame (price rows above price_lo, 2 decimals; -1 = no trades)."""
    vol = np.bincount(f, weights=sz, minlength=n)[:n]
    pv = np.bincount(f, weights=px * sz, minlength=n)[:n]
    return [round(float(a / v), 2) if v > 0 else -1 for a, v in zip(pv, vol)]


def backfill_vwap() -> None:
    """Add the per-frame volume-weighted price to clips exported before it existed, from the saved trades."""
    tests = pd.read_csv(OUT / "replay_tests.csv")
    for r in tests.itertuples():
        path = REPLAY_DIR / f"{r.id}.json"
        c = json.loads(path.read_text())
        src = (DERIVED / "es_weeks_trades" / f"{r.contract}_{r.day}.parquet" if r.market == "ES"
               else TRADES_OUT / f"{r.contract}_{r.day}.parquet")
        tr = pd.read_parquet(src)
        t_a, fs, n = c["start_epoch"], c["frame_seconds"], c["frames"]
        tr = tr[(tr["t"] >= t_a) & (tr["t"] < t_a + n * fs)]
        f = ((tr["t"] - t_a) // fs).astype(int).to_numpy()
        c["vwap"] = vwap_frames(f, tr["price"].to_numpy().astype(np.int64) - c["price_lo"], tr["size"].to_numpy(), n)
        path.write_text(json.dumps(c, separators=(",", ":")))
    print("added vwap to", len(tests), "clips")


def clip(book, typ: float, bursts: pd.DataFrame, x, meta: dict, ctx_candles: pd.DataFrame, off: int, R: dict,
         bubble_min: float) -> dict:
    fs, half = R["frame_seconds"], int(round(R["half_range_points"] / 0.25))
    t_a, t_b = x.time - R["before_seconds"], x.time + R["after_seconds"]
    n = int((t_b - t_a) // fs)
    p_lo = int(x.edge) - half
    prices = np.arange(p_lo, p_lo + 2 * half + 1)
    cols = np.clip(prices - book.lo, 0, book.bid.shape[1] - 1)
    rows = np.clip([book.row(t_a + (k + 1) * fs) for k in range(n)], 0, len(book.bid) - 1)
    bid, ask = book.bid[np.ix_(rows, cols)].astype(float), book.ask[np.ix_(rows, cols)].astype(float)
    full = R["depth_full_colour_x_typical"] * typ
    q = lambda v: np.round(127 * np.sqrt(np.clip(v / full, 0, 1))).astype(np.uint8)  # noqa: E731
    heat = np.where(ask > 0, q(ask) | 128, q(bid)).astype(np.uint8)          # high bit = ask side
    tr = book.trades[(book.trades["t"] >= t_a) & (book.trades["t"] < t_b)]
    f = ((tr["t"] - t_a) // fs).astype(int).to_numpy()
    px = tr["price"].to_numpy().astype(np.int64) - p_lo
    sz = tr["size"].to_numpy()
    buy = tr["side"].to_numpy() == "B"
    close, hi, lo = np.full(n, -1), np.full(n, -1), np.full(n, -1)
    bv, sv = np.zeros(n, int), np.zeros(n, int)
    for k in range(n):
        m = f == k
        if m.any():
            close[k], hi[k], lo[k] = px[m][-1], px[m].max(), px[m].min()
            bv[k], sv[k] = sz[m & buy].sum(), sz[m & ~buy].sum()
    vwap = vwap_frames(f, px, sz, n)
    for k in range(1, n):                                      # carry the last price through quiet frames
        if close[k] < 0:
            close[k], hi[k], lo[k] = close[k - 1], close[k - 1], close[k - 1]
    agg = pd.DataFrame(dict(f=f, p=px, b=buy, v=sz)).groupby(["f", "p", "b"])["v"].sum().reset_index()
    agg = agg[agg["v"] >= bubble_min].nlargest(R["max_bubbles"], "v")
    bu = bursts[(bursts["time"] >= t_a) & (bursts["time"] < t_b)]
    first_before = ctx_candles["start"] < t_a
    c = ctx_candles[first_before]
    return dict(
        id=meta["id"], market=meta["market"], contract=meta["contract"], weekday=meta["weekday"],
        test_time_et=meta["time_et"], direction=int(x.direction), frame_seconds=fs, frames=n,
        test_frame=int(R["before_seconds"] // fs), start_epoch=t_a, price_lo=int(p_lo), rows=len(prices),
        zone=dict(kind="demand" if x.direction == 1 else "supply", near=int(x.edge), lo=int(x.lo), hi=int(x.hi),
                  tf=meta["tf"], pattern=meta["pattern"], test=meta["zone_test"]),
        heat=b64(heat), close=close.tolist(), high=hi.tolist(), low=lo.tolist(), buy=bv.tolist(), sell=sv.tolist(),
        vwap=vwap,
        bubbles=agg[["f", "p", "b", "v"]].astype(int).values.tolist(),
        bursts=[[int((b.time - t_a) // fs), int(b.price - p_lo), int(b.contracts), int(b.direction)] for b in bu.itertuples()],
        context=dict(start=c["start"].astype(int).tolist(), o=(c["open"] - off).astype(int).tolist(),
                     h=(c["high"] - off).astype(int).tolist(), l=(c["low"] - off).astype(int).tolist(),
                     c=(c["close"] - off).astype(int).tolist()),
    )


def one_day(d: dict) -> list[dict]:
    s, R, zones, offsets, candles = CTX["s"], CTX["R"], CTX["zones"], CTX["offsets"], CTX["candles"]
    if d["es"] not in offsets.index:
        return []
    off = int(offsets[d["es"]])
    book = build_book(load_mbo(d["file"]), int(et(d["day"], "08:00")), int(d["end"]))
    if d["market"] == "MES":
        TRADES_OUT.mkdir(parents=True, exist_ok=True)
        book.trades.to_parquet(TRADES_OUT / f"{d['contract']}_{d['day']}.parquet", index=False)
    typ = typical_depth(book)
    start, end = et(d["day"], "09:30"), min(et(d["day"], "15:30"), d["end"] - R["after_seconds"])
    if end <= start:
        return []
    tests = find_tests(book, zone_levels(zones, off, start, end), start, end)
    if tests.empty:
        print(f"{d['day']} {d['market']}: no zone tests", flush=True)
        return []
    bmin = R["burst_min_es"] if d["market"] == "ES" else R["burst_min_mes"]
    bursts = day_bursts(book, R["burst_seconds"], bmin)
    rth = book.trades[(book.trades["t"] >= start) & (book.trades["t"] < end)]
    per = rth.assign(f=(rth["t"] // R["frame_seconds"]).astype(int)).groupby(["f", "price", "side"])["size"].sum()
    bubble_min = float(per.quantile(1 - R["bubble_top_share"])) if len(per) else 1.0
    day = pd.Timestamp(d["day"])
    sess = sorted(candles["session"].unique())
    k = sess.index(day) if day in sess else None
    keep = sess[max(0, k - R["context_sessions"]):k + 1] if k is not None else []
    ctx_c = candles[candles["session"].isin(keep)]
    out = []
    for n, x in enumerate(tests.itertuples()):
        z = zone_of(x, zones, off)
        loc = pd.Timestamp(x.time, unit="s", tz="UTC").tz_convert("America/New_York")
        meta = dict(id=f"{d['market']}-{d['day']}-{n:02d}", market=d["market"], contract=d["contract"],
                    weekday=loc.strftime("%A"), time_et=loc.strftime("%H:%M:%S"), **z)
        c = clip(book, typ, bursts, x, meta, ctx_c, off, R, bubble_min)
        REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        (REPLAY_DIR / f"{meta['id']}.json").write_text(json.dumps(c, separators=(",", ":")))
        out.append(dict(id=meta["id"], market=d["market"], contract=d["contract"], day=d["day"], time=x.time,
                        direction=int(x.direction), edge=int(x.edge), lo=int(x.lo), hi=int(x.hi), tf=z["tf"],
                        pattern=z["pattern"], zone_test=z["zone_test"], typical_size=typ))
    print(f"{d['day']} {d['market']}: {len(out)} clips", flush=True)
    return out


def main() -> None:
    s = load_settings()
    _, sessions, zones, candles = es_zones(s)
    CTX.update(s=s, R=s["replay"], zones=zones, offsets=sessions.groupby("contract")["offset_ticks"].first(),
               candles=candles[s["replay"]["context_minutes"]])
    with Pool(2) as pool:
        parts = pool.map(one_day, days())
    tests = pd.DataFrame([t for p in parts for t in p])
    tests.to_csv(OUT / "replay_tests.csv", index=False)
    print(len(tests), "clips;", tests.groupby("market").size().to_dict())


if __name__ == "__main__":
    import sys
    backfill_vwap() if "--backfill-vwap" in sys.argv else main()
