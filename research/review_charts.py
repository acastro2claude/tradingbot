"""Blind review charts: the owner marks take or skip on level tests, without seeing the outcome.

Each chart stops at the moment the bot would enter. It shows what the owner
said was missing from the first charts (Oct 2026): delta, volume, and where
the POCs and supply/demand zones are.
  - heatmap of the rebuilt ES book (resting contracts per price per second),
    trade dots, last price
  - the marked levels in view: the last 5 sessions' uncovered POCs (dashed) and
    live 15-minute supply/demand zones (shaded)
  - right: volume traded at each price today (from the evening open), with
    today's POC so far
  - bottom: cumulative delta (aggressive buys minus aggressive sells) since 09:30 ET

A random sample of tests (fixed seed) is drawn from level_tests_es_weeks.csv.
Charts go to research/results/journal/review/, numbered; the outcomes are kept
in review_key.csv for scoring after the owner answers.

Run from the repo root:   python -m research.review_charts [count]
"""

from __future__ import annotations

import sys

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.bot_week_study import BOOK_DIR, TRADES_DIR, et  # noqa: E402
from research.journal_replay import es_context  # noqa: E402
from research.journal_study import OUT  # noqa: E402
from research.level_test_book import marked_levels  # noqa: E402
from research.market_data import session_dates  # noqa: E402
from research.orderbook import Book, build_book, load_mbo  # noqa: E402
from research.orderbook_chart import BUY, INK, MUTED, RAMP, SELL, SURFACE  # noqa: E402
from research.settings import load_settings  # noqa: E402

REVIEW = OUT / "review"
DEMAND, SUPPLY = "#1baf7a", "#eb6834"
BEFORE_S = 900                     # 15 minutes of context before the decision
HALF_POINTS = 10.0


def draw_review(book: Book, trades: pd.DataFrame, levels: pd.DataFrame, t_dec: float, d: int, price: float,
                title: str, path) -> None:
    r1 = book.row(t_dec)
    r0 = max(r1 - BEFORE_S, 0)
    x0 = book.t0 + r0
    e = int(round(price / 0.25))
    p0, p1 = e - int(HALF_POINTS * 4), e + int(HALF_POINTS * 4)
    depth = book.bid[r0:r1 + 1, p0 - book.lo:p1 - book.lo + 1] + book.ask[r0:r1 + 1, p0 - book.lo:p1 - book.lo + 1]
    vmax = np.percentile(depth[depth > 0], 98) if (depth > 0).any() else 1

    fig = plt.figure(figsize=(12, 7.5), dpi=110, facecolor=SURFACE)
    gs = fig.add_gridspec(2, 2, width_ratios=[6, 1], height_ratios=[3, 1], wspace=0.03, hspace=0.06)
    ax = fig.add_subplot(gs[0, 0])
    axv = fig.add_subplot(gs[0, 1], sharey=ax)
    axc = fig.add_subplot(gs[1, 0], sharex=ax)
    for a in (ax, axv, axc):
        a.set_facecolor(SURFACE)
        a.tick_params(colors=MUTED, labelsize=8)
        for sp in a.spines.values():
            sp.set_visible(False)

    lo_px, hi_px = (p0 - 0.5) * 0.25, (p1 + 0.5) * 0.25
    ax.imshow(depth.T, origin="lower", aspect="auto", cmap=RAMP, vmin=0, vmax=vmax,
              extent=[0, r1 - r0 + 1, lo_px, hi_px], interpolation="nearest")

    # marked levels in view
    for lv in levels.itertuples():
        lo, hi = lv.lo * 0.25, lv.hi * 0.25
        if hi < lo_px or lo > hi_px:
            continue
        if lv.source == "POC":
            ax.axhline(lo, color=INK, lw=1.2, ls="--", zorder=2)
            ax.text(2, lo + 0.1, f"POC {lo:.2f}", color=INK, fontsize=8, zorder=5)
        else:
            colour = DEMAND if lv.direction == 1 else SUPPLY
            ax.axhspan(lo - 0.125, hi + 0.125, color=colour, alpha=0.18, zorder=2)
            ax.text(2, hi + 0.1, f"{'demand' if lv.direction == 1 else 'supply'} zone (15 min)",
                    color=INK, fontsize=8, zorder=5)

    w = trades[(trades["t"] >= x0) & (trades["t"] < t_dec)]
    ax.plot(w["t"] - x0, w["price"] * 0.25, color=MUTED, lw=1, drawstyle="steps-post", zorder=3)
    w = w[(w["price"] >= p0) & (w["price"] <= p1)].assign(sec=lambda f: np.floor(f["t"] - x0))
    agg = w.groupby(["sec", "price", "side"], as_index=False)["size"].sum()
    agg = agg[agg["size"] >= 20]
    for side, colour, marker in (("B", BUY, "^"), ("A", SELL, "v")):
        g = agg[agg["side"] == side]
        ax.scatter(g["sec"] + 0.5, g["price"] * 0.25, s=np.clip(g["size"], 20, 600) / 2, c=colour, marker=marker,
                   edgecolors=INK, linewidths=0.4, alpha=0.9, zorder=4)
    ax.annotate(f"bot would go {'long' if d == 1 else 'short'} here", xy=(r1 - r0, price),
                xytext=(r1 - r0 - 240, price + (3 if d == -1 else -3)), color=INK, fontsize=9,
                arrowprops=dict(arrowstyle="->", color=INK), zorder=6)
    ax.set_xlim(0, r1 - r0 + 30)
    ax.set_ylim(lo_px, hi_px)
    ax.set_ylabel("price", color=MUTED)
    ax.set_title(title, color=INK, loc="left", fontsize=11)
    plt.setp(ax.get_xticklabels(), visible=False)

    # today's volume at price, up to the decision
    today = trades[trades["t"] < t_dec]
    vol = today.groupby("price")["size"].sum()
    view = vol[(vol.index >= p0) & (vol.index <= p1)]
    axv.barh(view.index * 0.25, view.to_numpy(), height=0.22, color=MUTED, alpha=0.7)
    if len(vol):
        poc = vol.idxmax() * 0.25
        if lo_px <= poc <= hi_px:
            axv.axhline(poc, color=INK, lw=1)
            axv.text(view.max() * 0.05 if len(view) else 0, poc + 0.1, "today's POC", color=INK, fontsize=7)
    axv.set_xticks([])
    axv.set_xlabel("today's volume", color=MUTED, fontsize=8)
    plt.setp(axv.get_yticklabels(), visible=False)

    # cumulative delta since 09:30 ET
    open_t = et(pd.Timestamp(t_dec, unit="s", tz="UTC").tz_convert("America/New_York").date().isoformat(), "09:30")
    rth = trades[(trades["t"] >= open_t) & (trades["t"] < t_dec)]
    signed = np.where(rth["side"] == "B", rth["size"], np.where(rth["side"] == "A", -rth["size"], 0))
    cvd = pd.Series(np.cumsum(signed), index=rth["t"].to_numpy())
    cw = cvd[cvd.index >= x0]
    axc.plot(cw.index - x0, cw.to_numpy(), color=INK, lw=1.2)
    if len(cw) and cw.min() < 0 < cw.max():                 # zero line only when delta crosses it
        axc.axhline(0, color=MUTED, lw=0.6)
    axc.set_ylabel("cum. delta\nsince 09:30", color=MUTED, fontsize=8)
    ticks = np.arange((60 - x0 % 60) % 60, r1 - r0 + 1, 120)
    axc.set_xticks(ticks)
    axc.set_xticklabels([pd.Timestamp(x0 + k, unit="s", tz="UTC").tz_convert("America/New_York").strftime("%H:%M")
                         for k in ticks], color=MUTED)
    axc.set_xlabel("time (ET)", color=MUTED)
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    s = load_settings()
    tests = pd.read_csv(OUT / "level_tests_es_weeks.csv")
    plan = pd.read_csv(OUT / "es_week_price_plan.csv").set_index("day")
    e = tests[tests["turn"] == "turned"]
    pick = e.sample(count, random_state=s["level_study"]["random_seed"]).sort_values("entry_time")
    pick = pick.assign(review=range(1, len(pick) + 1))
    sessions, zones, offsets = es_context(s)
    REVIEW.mkdir(parents=True, exist_ok=True)
    for day, g in pick.groupby("day"):
        es = plan.loc[day, "es"]
        book = build_book(load_mbo(BOOK_DIR / f"{es}_{day}_mbo.dbn.zst"), int(et(day, "08:00")), int(et(day, "16:00")))
        trades = pd.read_parquet(TRADES_DIR / f"{es}_{day}.parquet")
        session = pd.Timestamp(session_dates(pd.Series([pd.Timestamp(et(day, "12:00"), unit="s", tz="UTC")])).iat[0])
        for r in g.itertuples():
            levels = marked_levels(sessions, zones, session, int(offsets[es]), r.entry_time - 1, r.entry_time,
                                   s["bot"]["recent_poc_lookback_sessions"])
            when = pd.Timestamp(r.entry_time, unit="s", tz="UTC").tz_convert("America/New_York")
            side = "long" if r.direction == 1 else "short"
            title = f"#{r.review}  {es} {when:%a %d %b %Y, %H:%M:%S} ET: take this {side}? ({r.sources} test)"
            draw_review(book, trades, levels, r.entry_time, int(r.direction), r.entry_price * 0.25, title,
                        REVIEW / f"review_{r.review:02d}.png")
            print("saved review", r.review, day, side, flush=True)
    pick[["review", "day", "entry_time", "direction", "sources", "edge", "points", "exit_how", "owner_points"]].to_csv(
        REVIEW / "review_key.csv", index=False)


if __name__ == "__main__":
    main()
