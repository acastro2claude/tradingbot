# The owner's journal, replayed against market data

Trades from the owner's TradesViz export (`pastData/`), Jan 2024 – Mar 2025, mostly 1-lot ES.
Times were fixed per trade against ES 1-minute bars (`time_fix.csv`).

## 1. The journal

513 closed trades over 150 days (`summary.csv`, `by_hour.csv`):

- Win rate 35%. Average win +4.9 points, average loss −2.2 (median win 4, median loss 2).
- +$5,309 after $1,522 in costs. Profit factor 1.13. 60% of days were winners. Max drawdown $2,888.
- Most trades were taken 9:30–11:00 ET. Median hold was about 3 minutes.
- **The 20 busiest days (163 trades, 6–20 a day) lost $6,015, with a 26% win rate. The other 130 days made about +$11,300.**

## 2. Replay against trade data (`replay_summary.csv`)

511 entries compared with 2,980 random moments in the same time windows. Your entries
did not stand out from random moments on absorption bursts, delta, the move into the
entry, POCs, POC clusters, S/D zones, or classic levels. Winners and losers looked the
same. The owner then confirmed the setup is read mainly from the order book (heatmap),
which trade data can't show.

## 3. Order book, first look (`orderbook_*.csv`, Oct 2026)

**Data:** the full MES order book (Databento MBO, order by order, $8.34) for the 20 busiest
journal days, rebuilt second by second the way Bookmap's heatmap draws it. The rebuild
checks out: the book never crosses, never goes negative, and trades print at the best
bid or ask. `orderbook_charts/` has Bookmap-style pictures of single trades to check by
eye (`python -m research.orderbook_chart 2024-03-25 0`).

**Terms.** For a short, the *wall* is the asks above price (what the trade leans on) and
the *path* is the bids below (what's in the way of the target). For a long it's the other
way round. A *line* is a single price with at least 2× (or 3×) the day's typical size per
price near the market. On MES the typical size was about 100–180 contracts, and 26 on
the one 2025 day.

**Your entries vs random moments.** Random moments come from the same day and
were matched on how price moved in the last 5 seconds, 30 seconds and 2 minutes. The
ranges are 95% ranges from resampling whole days.

| Reading | Your entries | Random, matched | Difference range |
|---|---|---|---|
| Path orders pulled faster than added (last 30 s, within 2 pts) | 51% | 40% | +3 to +18 |
| More size on the wall than in the path (within 5 pts) | 28% | 35% | −12 to −2 |
| More size on the wall than in the path (within 2 pts) | 25% | 32% | −14 to +1 |
| Line 2×+ on the wall within 2 pts | 1% | 3% | −4 to −1 |
| Line 3×+ on the wall within 5 pts | 18% | 19% | −6 to +3 |
| Wall kept refilling (filled more than it ever showed, last 2 min) | 85% | 88% | −10 to +3 |
| 2-pt stop / 4-pt target reached first | 24% | 32% | −13 to −4 |

- **You entered once price had already started your way.** In the last 5 seconds the median move was 0.5 points in the trade's direction, so these are confirmation entries, not resting orders at a level.
- **At your entries, the orders in the path were being pulled.** For a short, that means the bids below were pulled faster than new ones arrived. This is the one reading that clearly stood out.
- The biggest wall order within 2 points was newer at your entries (median 38 s old vs 70 s).
- **You were almost never entering next to a big visible line on MES.** Big lines showed up within 5 points about as often as at random moments.
- On these days your entries did worse than random moments with the same move (24% vs 32% reaching 4 points before 2 against).
- **None of these readings separated your winners from your losers.**

**Does any book reading help on its own?** This test used 6,000 random moments with a
random direction. The question was how often price reached 4 points in favour before
2 against, with and without each reading (`orderbook_edge.csv`). Base rate: 30%.

- None of the readings moved the rate by more than a few points, except in groups too small to judge (under 50 moments).
- A big line on the wall side within 2 points did *not* hold price. Only 25% reached the target, vs 31% without the line (302 moments). With a 3×+ line: 18% (34 moments). On MES in these days, price was more often drawn to big lines than turned by them.

## Caveats

- These are the busiest days, which were also the losing days. They show what the book looked like when trading went badly, not when it went well.
- The book is MES. The journal trades are mostly ES, and in 2024 the owner may have watched the ES book in Bookmap. The ES book is deeper, and big players' lines show there.
- 163 trades over 20 days is a small sample. Differences under about 10 points are within noise.

## Possible next steps (each needs the owner's OK)

- ES full book for the same 20 days ($10.10), if the 2024 trading was read off the ES heatmap.
- MES full book for the 20 best days with 3+ trades ($8.78, 85 trades, 39% win rate) to see the book when trading went well.
- Sharper definitions from the owner of the book reads (for example: what size makes a line, and what "pulled" or "refilled" looks like on screen), checked against the charts in `orderbook_charts/`.
