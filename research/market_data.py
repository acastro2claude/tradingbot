"""Load Databento files and organise them into Globex sessions.

Prices are kept as whole ticks (price / 0.25) so they compare exactly.

Contract rolls: each session uses one contract only, the one with the most
volume that session. Older contracts are shifted by the roll gap (the price
difference between the new and old contract on roll day) so every session sits
on one continuous price scale, like a back-adjusted chart. `offset_ticks` is
the shift added to a session's raw prices; it is 0 for the latest contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.settings import load_settings

NY = "America/New_York"


def to_ticks(prices, tick_size: float) -> np.ndarray:
    return np.rint(np.asarray(prices, dtype=float) / tick_size).astype(np.int64)


def session_dates(ts_utc: pd.Series, start_hour: int = 18) -> pd.Series:
    """Session date for each UTC timestamp (naive midnight of the session's day).

    Times at or after 18:00 ET belong to the next calendar day's session. The
    date math is done on the local calendar date, not by adding 24 hours, so
    daylight saving changes are handled.
    """
    et = ts_utc.dt.tz_convert(NY)
    local_date = et.dt.tz_localize(None).dt.normalize()
    return local_date + pd.to_timedelta((et.dt.hour >= start_hour).astype(int), unit="D")


def load_symbology(path: Path) -> pd.DataFrame:
    """Date ranges for which each instrument ID meant each contract (IDs get reused)."""
    raw = json.loads(Path(path).read_text())
    rows = [(int(iv["s"]), pd.Timestamp(iv["d0"]), pd.Timestamp(iv["d1"]), sym)
            for sym, ivs in raw.items() for iv in ivs]
    return pd.DataFrame(rows, columns=["instrument_id", "d0", "d1", "contract"])


def _attach_contract(df: pd.DataFrame, symbology: pd.DataFrame) -> pd.DataFrame:
    udate = df["ts"].dt.tz_localize(None).dt.normalize()
    keys = pd.DataFrame({"instrument_id": df["instrument_id"].values, "udate": udate.values})
    uniq = keys.drop_duplicates().merge(symbology, on="instrument_id", how="left")
    uniq = uniq[(uniq["udate"] >= uniq["d0"]) & (uniq["udate"] < uniq["d1"])]
    out = keys.merge(uniq[["instrument_id", "udate", "contract"]],
                     on=["instrument_id", "udate"], how="left")
    df = df.copy()
    df["contract"] = out["contract"].values
    return df


def load_bars(dbn_path: Path, symbology_path: Path, settings: dict | None = None) -> pd.DataFrame:
    """1-minute bars for outright contracts, prices in ticks, with session dates."""
    import databento as db

    s = settings or load_settings()
    tick = s["market"]["tick_size"]
    store = db.DBNStore.from_file(dbn_path)
    raw = store.to_df(map_symbols=False, price_type="float", pretty_ts=True).reset_index()
    df = pd.DataFrame({
        "ts": raw["ts_event"],
        "instrument_id": raw["instrument_id"].astype(np.int64),
        "open": to_ticks(raw["open"], tick),
        "high": to_ticks(raw["high"], tick),
        "low": to_ticks(raw["low"], tick),
        "close": to_ticks(raw["close"], tick),
        "volume": raw["volume"].astype(np.int64),
    })
    df = _attach_contract(df, load_symbology(symbology_path))
    if df["contract"].isna().any():
        raise ValueError(f"{df['contract'].isna().sum()} bars have no contract mapping")
    df = df[~df["contract"].str.contains("-")]  # drop calendar spreads
    df["session"] = session_dates(df["ts"])
    return df.sort_values(["ts", "contract"]).reset_index(drop=True)


def load_trades(dbn_path: Path, settings: dict | None = None) -> pd.DataFrame:
    """Trades for one contract: time, price in ticks, size, aggressor side."""
    import databento as db

    s = settings or load_settings()
    store = db.DBNStore.from_file(dbn_path)
    raw = store.to_df(map_symbols=False, price_type="float", pretty_ts=True).reset_index()
    df = pd.DataFrame({
        "ts": raw["ts_event"],
        "price": to_ticks(raw["price"], s["market"]["tick_size"]),
        "size": raw["size"].astype(np.int64),
        "side": raw["side"].astype(str),
    })
    df["session"] = session_dates(df["ts"])
    return df


def _hhmm(text: str) -> tuple[int, int]:
    h, m = text.split(":")
    return int(h), int(m)


def build_sessions(bars: pd.DataFrame, degraded_utc_dates=(), settings: dict | None = None
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pick one contract per session, measure roll gaps, flag sessions to skip.

    Returns (front_bars, sessions). front_bars has the bars of each session's
    contract plus adjusted prices (`*_adj`, raw + offset_ticks).
    """
    s = settings or load_settings()
    ls = s["level_study"]

    vol = bars.groupby(["session", "contract"])["volume"].sum().unstack(fill_value=0)
    front = vol.idxmax(axis=1).rename("contract")
    sessions = front.to_frame()
    sessions["volume"] = vol.max(axis=1)

    # Roll gaps: on the session where the front contract changes, the median
    # close-to-close difference over minutes when both contracts traded.
    gaps = {}
    prev = None
    for sess, contract in front.items():
        if prev is not None and contract != prev:
            day = bars[bars["session"] == sess]
            a = day[day["contract"] == prev].set_index("ts")["close"]
            b = day[day["contract"] == contract].set_index("ts")["close"]
            both = pd.concat([a, b], axis=1, join="inner")
            if len(both) < 30:
                raise ValueError(f"only {len(both)} shared minutes to measure the {prev}->{contract} roll")
            gaps[contract] = int(np.rint(np.median(both.iloc[:, 1] - both.iloc[:, 0])))
        prev = contract

    # Offsets: latest contract 0; each older one shifted by all later gaps.
    order = list(dict.fromkeys(front.values))
    changes = int((front != front.shift()).sum()) - 1
    if changes != len(order) - 1:
        raise ValueError("front contract switched back and forth; roll handling needs a rule for that")
    offsets = {order[-1]: 0}
    for older, newer in zip(order[-2::-1], order[:0:-1]):
        offsets[older] = offsets[newer] + gaps[newer]
    sessions["offset_ticks"] = sessions["contract"].map(offsets).astype(np.int64)
    sessions["roll_gap_ticks"] = [gaps.get(c) if (i > 0 and c != front.iloc[i - 1]) else np.nan
                                  for i, c in enumerate(front.values)]

    fb = bars.merge(sessions[["contract"]].reset_index(), on=["session", "contract"], how="inner")
    fb = fb.merge(sessions[["offset_ticks"]].reset_index(), on="session")
    for col in ("open", "high", "low", "close"):
        fb[f"{col}_adj"] = fb[col] + fb["offset_ticks"]

    # Flags: short (holiday / early close) and degraded data.
    et = fb["ts"].dt.tz_convert(NY)
    last = et.groupby(fb["session"]).max()
    h, m = _hhmm(ls["short_session_last_bar_before_et"])
    last_minutes = last.dt.hour * 60 + last.dt.minute
    sessions["first_bar_et"] = et.groupby(fb["session"]).min().dt.strftime("%Y-%m-%d %H:%M")
    sessions["last_bar_et"] = last.dt.strftime("%Y-%m-%d %H:%M")
    sessions["short"] = last_minutes < h * 60 + m
    bad = {pd.Timestamp(d) for d in degraded_utc_dates}
    sessions["degraded"] = [(d in bad) or (d - pd.Timedelta(days=1) in bad) for d in sessions.index]
    sessions["use"] = ~sessions["short"] if ls["exclude_short_sessions"] else True
    sessions.index.name = "session"
    return fb.sort_values("ts").reset_index(drop=True), sessions
