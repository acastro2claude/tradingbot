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

## 4. ES book, five whole weeks: your read at marked levels, and the bot vs you (Oct 2026)

**Data:** the full ES order book for whole regular sessions in five 2024 weeks, chosen to
mix good and bad weeks for the owner: Feb 26, Apr 8, Jun 3, Jul 29 and Sep 23. That's
23 days, because 2 Aug and 27 Sep were not downloaded (dropped connections, see
data-spend.md). The owner watched the ES heatmap in 2024. Code:
`research/level_test_book.py` and `research/bot_week_study.py` (add `--reuse` to skip
rebuilding the books). Results: `level_tests_es_weeks*.csv` and `bot_vs_owner_*.csv`.

**The read as coded (proposed, from the owner's description in STRATEGY.md section 2):**
- **Marked levels:** the last 5 sessions' uncovered POCs and 15-minute supply/demand zones, from ES 1-minute bars.
- **A test:** price trades at the level after being 5+ points away.
- **"At or around" the level:** within 1 point of it.
- **Line:** 2× the day's typical size per price, there in the 5 minutes before the test. It "held" if half or more was still there when price arrived.
- **Eaten:** contracts filled at the level during the test. **Stacking:** more added than pulled during the test.
- **Delta:** the last 10 seconds, the trade's way.
- **Absorption:** a fitting burst of 50+ contracts at the level (ES setting TBD; the MES add-on uses 100).
- **Entry:** once price trades 1 point back off the test's extreme, before going 3 points through the level.
- **Exits:** stop 2 points and target 4 points from the fill (your median loss and win), with 1 tick slippage on entries and stops, and $12.40 costs (10 MES).

**Every test, traded this way (221 tests, `level_tests_es_weeks_after_costs.csv`).** With
these exits, break-even needs about a 40% win rate.

| Read at the test | Tests | Win rate | $ per trade (1 ES) |
|---|---|---|---|
| Every test of a marked level | 221 | 27% | −41 |
| Line there and held | 99 | 25% | −46 |
| Line held + eaten + stacking | 80 | 28% | −39 |
| Full read (+ delta + absorption) | 25 | 24% | −50 |
| Eaten + stacking (no line needed) | 179 | 30% | −32 |
| Line before + stacking + delta | 38 | 32% | −26 |
| POC tests | 142 | 30% | −30 |
| Zone tests | 79 | 20% | −62 |
| Tests you traded | 11 | 18% | −68 |

- Without slippage, the same reads lean your way: line before + stacking + delta reached 4 points before 2 against 37% of the time, vs 30% for the others (`level_tests_es_weeks_summary.csv`). But it is never enough to pay for the stop.
- A POC and a zone never lined up at the same test in these weeks.
- **You traded only 13 of these 230 tests.** 115 of your 126 trades on these days were somewhere else. That fits your answer that in 2024 you used these levels only partly.

**Bot vs you, same 23 days.** The bot takes up to 3 trades a day, one at a time, 10 MES (= 1 ES).

| Week (2024) | You: trades, net | Bot, every level test | Bot, eaten + stacking, 9:30–11:00 |
|---|---|---|---|
| Feb 26 | 48, −$2,525 | 15, +$314 | 15, +$314 |
| Apr 8 | 26, +$22 | 15, −$624 | 12, −$249 |
| Jun 3 | 20, +$649 | 15, −$1,249 | 7, −$562 |
| Jul 29 (no Aug 2) | 12, +$844 | 12, −$561 | 7, −$249 |
| Sep 23 (no Sep 27) | 20, −$400 | 12, +$376 | 11, +$189 |
| **Total** | **126, 34% wins, −$1,410** | **69, 32%, −$1,743** | **52, 37%, −$557** |

- Every bot version lost over the five weeks. The full read lost $1,185 on 22 trades (23% wins).
- The bot did better than you in your two losing weeks and worse in your three winning weeks.
- The bot and you rarely took the same trade (7 of the bot's 69).

**Charts to check by eye:** `orderbook_charts/bot_*.png` show full-read wins and losses,
and tests you traded, on the rebuilt ES heatmap with your trades nearby.

## 5. Exit styles on the same tests (`exit_study_*.csv`, Oct 2026)

Every test that turned was traded with each exit style in `[[book_study.exits]]`
(settings.toml). Targets fill only when price trades 1 tick through them. Stops pay
1 tick of slippage. Code: `research/exit_study.py`.

| Exit style | Tests traded | Win rate | Avg win / loss (pts) | $ per trade (1 ES) |
|---|---|---|---|---|
| 2-pt stop, 4-pt target | 221 | 25% | +4.0 / −2.25 | −47 |
| 3-pt stop, 7-pt target (owner) | 221 | 21% | +6.9 / −3.25 | −70 |
| Stop 1 pt past the test, target 2× the risk | 221 | 25% | +4.1 / −2.45 | −53 |
| Stop 1 pt past the test, STRATEGY.md targets | 116 | 12% | +9.8 / −2.25 | −52 |
| Stop 3 pts past the level, STRATEGY.md targets | 76 | 24% | +11.8 / −3.7 | −15 |

- STRATEGY.md targets: the first target is the top of the last 1-minute chop before the move into the level, and the runner targets the low of that chop and breakout. The trade is skipped when the first target is under 2× the risk, which removed about two thirds of the tests.
- **No exit style made the read profitable.** The closest was the STRATEGY.md exits with the stop 3 points past the level: −$15 per trade. The bot version (one trade at a time) made +$362 over 42 trades. But two runners (34 and 19 points) carry that result, and 3 of the 5 weeks lost.
- **Adding the read's conditions (line held, eaten, stacking, delta, absorption) made results worse under every exit.** The full read was usually the worst. This suggests the coded read is not what the owner does, which is why the blind review below comes next.

**Orders that move with price (checked Oct 2026).** On the owner's request, the moving
"channel" of resting orders (for example 30 Jul 2024, 10:04–10:18) was checked against
the order-by-order data. It is real, not a data error:
- Specific orders of exactly 150 contracts were moved 20–32 times each to follow price.
- Bids stayed 3.25–3.5 points below the last trade and asks 3.5 points above.
- A dozen or so stacked on each side.

The owner says moving orders can still count. `line_sat_s` records how long a line sat at
one price, so the two can be compared.

**Blind review (next).** `review/review_*.png` are 12 tests drawn at random. Each chart
stops at the moment the bot would enter, and shows the heatmap, the marked POCs and
zones, today's volume profile, and cumulative delta. The owner marks take or skip, and
`review/review_key.csv` holds the outcomes for scoring afterwards.

## Caveats

- The read is my translation of the owner's description. Every threshold above is a proposal, and the owner's eye may weigh things differently.
- 23 days and 221 tests is still small. Differences under about 10 points are noise.
- The exits are fixed at 2 and 4 points. The owner's own exits were managed (average win 4.9 points).
- Sections 2–3: the 20 busiest days were also the losing days, and that book was MES.

## Possible next steps (each needs the owner's OK)

- The owner checks the charts, ideally next to their own Bookmap recordings of the same days if they have them, and says where my read differs from theirs.
- The ES setting of the absorption add-on, and the size that makes a "line" on the owner's screen.
