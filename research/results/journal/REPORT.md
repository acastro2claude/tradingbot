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
- **Entry:** once price trades 1 point back off the test's extreme, before going 3 points through the level. The fill is the price of that trade plus 1 tick of slippage. An earlier version took the book price a second before, which flattered fills by 1–2 ticks on fast moves. Fixed 29 Oct; all numbers below are after the fix.
- **Exits:** stop 2 points and target 4 points from the fill (your median loss and win), with 1 tick slippage on entries and stops, and $12.40 costs (10 MES).

**Every test, traded this way (221 tests, `level_tests_es_weeks_after_costs.csv`).** With
these exits, break-even needs about a 40% win rate.

| Read at the test | Tests | Win rate | $ per trade (1 ES) |
|---|---|---|---|
| Every test of a marked level | 221 | 24% | −51 |
| Line there and held | 99 | 23% | −52 |
| Line held + eaten + stacking | 80 | 24% | −51 |
| Full read (+ delta + absorption) | 25 | 20% | −62 |
| Eaten + stacking (no line needed) | 179 | 25% | −46 |
| Line before + stacking + delta | 38 | 24% | −51 |
| POC tests | 142 | 28% | −37 |
| Zone tests | 79 | 15% | −77 |
| Tests you traded | 11 | 18% | −68 |

- Without slippage, the same reads lean your way: line before + stacking + delta reached 4 points before 2 against 34% of the time, vs 28% for the others (`level_tests_es_weeks_summary.csv`). But it is never enough to pay for the stop.
- A POC and a zone never lined up at the same test in these weeks.
- **You traded only 13 of these 230 tests.** 115 of your 126 trades on these days were somewhere else. That fits your answer that in 2024 you used these levels only partly.

**Bot vs you, same 23 days.** The bot takes up to 3 trades a day, one at a time, 10 MES (= 1 ES).

| Week (2024) | You: trades, net | Bot, every level test | Bot, eaten + stacking, 9:30–11:00 |
|---|---|---|---|
| Feb 26 | 48, −$2,525 | 15, +$2 | 15, +$2 |
| Apr 8 | 26, +$22 | 15, −$624 | 12, −$249 |
| Jun 3 | 20, +$649 | 15, −$1,561 | 7, −$562 |
| Jul 29 (no Aug 2) | 12, +$844 | 12, −$561 | 7, −$249 |
| Sep 23 (no Sep 27) | 20, −$400 | 12, +$64 | 11, −$124 |
| **Total** | **126, 34% wins, −$1,410** | **69, 28%, −$2,681** | **52, 33%, −$1,182** |

- Every bot version lost over the five weeks. The full read lost $1,498 on 22 trades (18% wins).
- The bot did better than you in your two losing weeks and worse in your three winning weeks.
- The bot and you rarely took the same trade (6 of the bot's 69).

**Charts to check by eye:** `orderbook_charts/bot_*.png` show full-read wins and losses,
and tests you traded, on the rebuilt ES heatmap with your trades nearby.

## 5. Exit styles on the same tests (`exit_study_*.csv`, Oct 2026)

Every test that turned was traded with each exit style in `[[book_study.exits]]`
(settings.toml). Targets fill only when price trades 1 tick through them. Stops pay
1 tick of slippage. Code: `research/exit_study.py`.

| Exit style | Tests traded | Win rate | Avg win / loss (pts) | $ per trade (1 ES) |
|---|---|---|---|---|
| 2-pt stop, 4-pt target | 221 | 23% | +4.0 / −2.25 | −54 |
| 3-pt stop, 7-pt target (owner) | 221 | 19% | +6.9 / −3.25 | −79 |
| Stop 1 pt past the test, target 2× the risk | 221 | 24% | +4.6 / −2.5 | −54 |
| Stop 1 pt past the test, STRATEGY.md targets | 109 | 11% | +10.4 / −2.5 | −67 |
| Stop 3 pts past the level, STRATEGY.md targets | 71 | 23% | +12.1 / −4.0 | −30 |

- STRATEGY.md targets: the first target is the top of the last 1-minute chop before the move into the level, and the runner targets the low of that chop and breakout. The trade is skipped when the first target is under 2× the risk, which removed about two thirds of the tests.
- **No exit style made the read profitable.** The closest was the STRATEGY.md exits with the stop 3 points past the level: −$30 per trade. The bot version (one trade at a time) made +$295 over 40 trades. But a couple of big runners carry that result, and 3 of the 5 weeks lost.
- **Adding the read's conditions (line held, eaten, stacking, delta, absorption) made results worse under every exit.** The full read was usually the worst. This suggests the coded read is not what the owner does, which is why the blind review below comes next.

**Why the exits lose (`exit_stop_check.csv`, `python -m research.exit_stop_check`).**
- **Almost every losing trade is a stop-out.** 76–89% of trades end at the stop, depending on the exit style. Only 0–1% are flattened at the close, so the losses are not small end-of-day exits.
- **The 2-point stop is hit fast.** Median 1.2 minutes after entry. 53% of those trades went 1+ point our way first.
- **Price often comes back.** 56% of all tests were stopped at 2 points and then traded through the 4-point target later the same day. 79% reached +4 at some point before the close, but usually only after going against the trade first (median 4 points; 47% went 5+ points against).
- **A wider stop doesn't fix it.** The win rate rises with the stop, but never as fast as break-even. And at every stop size, the level tests won 4–9 points *less* often than random entries on the same days (`exit_stop_check_summary.csv`):

| Stop / target (pts) | Level tests won | Random entries won | Break-even |
|---|---|---|---|
| 2 / 4 | 23% | 27% | ~40% |
| 3 / 7 | 19% | 24% | ~34% |
| 3 / 4 | 29% | 38% | ~48% |
| 4 / 4 | 38% | 46% | ~55% |
| 5 / 4 | 42% | 51% | ~59% |

So the problem is the entry, not the exit. Entering on the first 1-point turn at a marked level, price tends to push further through the level before it reverses.

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
zones, today's volume profile, buy vs sell volume every 15 seconds, and cumulative delta
(aqua for buyers, orange for sellers). The owner marks take or skip, and
`review/review_key.csv` holds the outcomes for scoring afterwards.

## 6. Blind review and the owner's rules (Oct 2026)

**The review (`review/review_answers.csv`).** The owner marked 12 random test charts that
stop at the entry. Four were takes (#3, #7, #9, #10) and eight were skips (#1 as "wait").
What price did next (best and worst point within 30 minutes, in points):

| # | Owner | Best | Worst | Owner's target |
|---|---|---|---|---|
| 1 | wait (long) | 3.5 | 5.5 | — |
| 2 | skip (long) | 3.75 | 6.0 | — |
| 3 | **take (short)** | 10.0 | 2.75 | 5281: reached in 7 min, 2.75 against first |
| 4 | skip (long) | 1.25 | 6.5 | — |
| 5 | skip (short) | 7.25 | 1.0 | — |
| 6 | skip (long) | 4.0 | 13.25 | — |
| 7 | **take (short)** | 4.0 | 7.0 | 5460.5: not reached |
| 8 | skip (short) | 17.0 | 13.75 | — |
| 9 | **take (short)** | 15.0 | 8.0 | next POC 5500: reached in 16 min, 8 against first |
| 10 | **take (long, continuation)** | 2.0 | 21.0 | not reached |
| 11 | skip (short) | 6.0 | 20.75 | — |
| 12 | skip (long) | 7.0 | 5.75 | — |

- **The skips avoided the trades that ran hard against** (#2, #4, #6, #11: 6–21 points within 30 minutes).
- #5 was a clean winner that got skipped.
- Two of the four takes reached the owner's target.
- 12 charts is too few to measure skill, but the reasons were clear enough to code (STRATEGY.md section 2).

**The reasons as rules (`research/owner_rules.py`, `owner_rules_*.csv`, `[owner_rules]` in
settings.toml, all proposed).** Results on all 221 tests:

| Rule | Tests | Win 2/4 | $/trade 2/4 |
|---|---|---|---|
| Every test (first 1-point turn) | 221 | 23% | −54 |
| Other side not in control (no new delta extreme against in 30 s) | 59 | 20% | −61 |
| Retest of the level, push weaker than last time | 47 | **34%** | **−19** |
| Retest of the level, push not weaker | 40 | 15% | −78 |
| Absorption spike (2× volume into the level, price ≤ 1 pt past) | 106 | 29% | −34 |
| Delta divergence at the 30-minute extreme | 27 | 26% | −44 |
| Patient entry (wait until delta and aggression turn, up to 10 min) | 165 | 22% | −57 |

- **The "other side still in control" checks are true at about 3 in 4 first-turn entries.** In the owner's words, the bot gets in "too soon".
- **Waiting for control to pass (patient entry) did not help.**
- With the owner's targets (next POC, 3-point stop), every rule did worse than with 2/4.
- **The weaker retest is the clearest signal found so far.** It is still not profitable, and the gap between 34% and 15% is borderline on these numbers. It is also the retest rule already in STRATEGY.md section 2, and it uses trade data only, so it can be checked on the 2026 MES trades already downloaded at no cost.
- On the 12 review charts, the coded filter skipped 11, including three of the owner's takes. In #9, the owner read a buying spike with no progress as absorption, where the coded "aggression" check read it as buyers in control.

## 7. Check on 2026: does the weaker retest hold up? (`retest_2026_*.csv`, Oct 2026)

The same tests, with the same levels, entries and exits, were run on the MES trades
already downloaded for step 2. That's regular hours, late Apr to late Oct 2026, 126
sessions and 1,359 level tests. Levels come from the MES 1-minute bars. There was no
cost. Code: `research/retest_2026.py`.

| Group | Tests | Win 2/4 | $/trade 2/4 | Win, 3-pt stop + next POC | $/trade |
|---|---|---|---|---|---|
| Every test (first 1-point turn) | 1,359 | 25% | −47 | 25% | −50 |
| First test of the level that session | 593 | 23% | −54 | 24% | −54 |
| Retest, push weaker than last time | 333 | 29% | −35 | 27% | −41 |
| Retest, push not weaker | 433 | 25% | −46 | 26% | −50 |
| Absorption spike | 690 | 25% | −47 | 25% | −49 |
| Delta divergence | 105 | 27% | −42 | 22% | −67 |
| POC tests | 859 | 23% | −53 | 24% | −59 |
| Zone tests | 497 | 28% | −36 | 28% | −34 |
| Random entries, same days | 12,600 | 27% | | | |

- **The weaker retest still leads, but by much less.** In 2026 it won 29% against 25% for retests that were not weaker, a gap of +4 points (95% range −3 to +11). In 2024 the gap was 19 points. The 2024 gap was mostly luck of a small sample.
- **Level tests as a whole did no better than random entries** (25% vs 27%). Nothing came close to break-even (about 40% with these exits).
- POC tests beat zone tests in 2024. In 2026 it was the other way round, so neither holds.

## 8. Blind review, second round (`review/batch2/`, `review_score*.csv`, Oct 2026)

**Setup.** 40 new random level tests from the five 2024 ES weeks were shown on a review
page (Take / Skip / Wait, a reason, an optional target). The owner took 19 and skipped 21,
with a reason for each (`review/batch2/answers.csv`). Scoring starts from the bot's fill
at the chart's last moment (`research/review_score.py`).

| | Charts | 2-pt stop, 4-pt target | 3-pt stop, 6-pt target | 3-pt stop, owner's target | Median best / worst, 30 min |
|---|---|---|---|---|---|
| Takes (round 2) | 19 | 16% win, −$76/trade | 16%, −$102 | 16%, −$89 | +4.75 / −7.75 pts |
| Skips (round 2) | 21 | 24% win, −$50/trade | 29%, −$43 | 29%, −$43 | +4.0 / −6.75 pts |
| Takes, both rounds | 23 | 13% | 17% | 17% | |
| Skips, both rounds | 29 | 24% | 28% | 28% | |

- **The takes did not beat the skips.** The gap goes the other way, but it is within chance (Fisher p ≈ 0.7 for round 2, 0.5 for both rounds).
- Three takes worked well: #9, #10 and #23, which went +16, +18 and +37 points within 30 minutes. Several went 13–20 points against: #6, #12, #16, #20 and #26.
- **New rules from the reasons:**
  - no trades in the minutes after news, including the 10:00 ET reports (#2, #6, #11, #13, #30)
  - nothing too soon after the open (#38)
  - short into supply when big buying volume fails to lift price (#4, #17)
  - delta divergence at a zone (#15, #25, #27, #33)
  - a weaker retest (#18)

  These were added to STRATEGY.md where they are rules.

**Caveats.**
- A static chart is not live Bookmap. The owner noted they can't see the book move ("since it's not live I can't tell exactly").
- The entry moment and the stop were the bot's. Several answers said the entry was premature.
- The levels are the bot's, which the owner used only partly in 2024.

## 9. Day rules replayed on the owner's journal (`journal_day_rules.csv`, Oct 2026)

All 513 journal trades were replayed under day rules (`research/journal_day_rules.py`,
grids in `[journal_rules]`). Each rule only decides which trades would have been taken;
each trade keeps its real result after costs. News minutes come from the ES data itself: a
volume spike at 08:30, 10:00 or 14:00 ET, at least 3× the 10 minutes before. The detector
fired on every CPI and jobs-report day at 08:30 (11–32×, against a typical 2×) and on
Fed days at 14:00. 36 days had a 10:00 spike.

| Rule | Trades | Net $ | Change | First half | Second half | Profit factor | Max drawdown |
|---|---|---|---|---|---|---|---|
| As traded | 513 | +5,309 | | | | 1.13 | 2,888 |
| At most 1 trade a day | 150 | +240 | −5,069 | −625 | −4,444 | 1.02 | 2,685 |
| At most 3 trades a day | 344 | −196 | −5,505 | −1,914 | −3,591 | 0.99 | 3,592 |
| Stop after 2 losing trades | 296 | −856 | −6,165 | −2,447 | −3,718 | 0.96 | 3,595 |
| Daily loss limit $250 | 364 | −937 | −6,247 | −892 | −5,354 | 0.97 | 3,686 |
| Daily loss limit $800 | 496 | +3,989 | −1,320 | −776 | −544 | 1.11 | 2,939 |
| Stop once up $150 on the day | 491 | +5,251 | −58 | −163 | +105 | 1.14 | 2,763 |
| No entries in the first 5 min | 505 | +5,584 | +275 | +148 | +128 | 1.14 | 2,938 |
| No entries in the first 15 min | 469 | +5,746 | +437 | −50 | +488 | 1.16 | 3,039 |
| **No entries 10 min after a news spike** | 491 | **+6,618** | **+1,309** | +272 | +1,038 | 1.18 | 2,663 |
| Mornings only (09:30–11:00) | 464 | +5,276 | −33 | +509 | −542 | 1.15 | 2,009 |
| **First 3 trades of each day on sim** | 169 | +5,505 | +196 | +124 | +72 | **1.46** | **2,331** |
| First 3 trades of each day at half size | 513 | +5,407 | +98 | +62 | +36 | 1.21 | 2,319 |

**Why caps and loss stops hurt.** How many trades the owner took on a day mostly
reflects how the first ones went:
- **Days with 1–2 trades (won early, stopped): 70 days, +$13,193.**
- **Days with 4+ trades: 61 days, −$8,679.**
- On days with 6+ trades, the first three trades lost $6,429.
- **Trades 4–10 made +$7,015** on 154 trades (41% wins, +1.0 to +1.6 points per contract). Without the 5 biggest winners it's still +$3,307.

So a cap keeps the early losers and cuts the later trades that recovered. The pattern
holds in both halves of the period:
- **Trades after the first three:** +$1,914 and +$3,591.
- **The first three:** −$124 and −$72.

It holds within the same time windows too. It's not size (about 1 ES throughout), and not
coming back to the same price. Trades taken when the day was already down $300+ beat the
rest by 0.3–2.6 points per contract (95% range from resampling days).

**What it means.**
- **The owner's first ~3 trades of a day had no edge** (344 trades, −$196). The edge came after (169 trades, +$5,505).
- **Taking the first three at half size or on sim leaves the money about the same, with less risk.** On sim, the profit factor goes 1.13 → 1.46 and the drawdown $2,888 → $2,331, with a third of the trades.
- **Of the rules here, only the news filter clearly adds money:** +$1,309, in both halves.
- **The daily trade cap of 1 in STRATEGY.md would have cut this journal to +$240.** That's a point to weigh, though the bot is not the owner.
- **Caveat:** several of these rules were found by looking at this same journal. The split-half check helps, but the owner's future trades are the real test.

## 10. What made the owner's later trades better? (`journal_context.csv`, `journal_opening_range.csv`)

Owner's question: if the later trades are the profitable ones, why take the first ones,
and is it a time of day? Code: `research/journal_context.py`.

**Why not just skip ahead.** The later trades mostly exist because the early ones lost:
- On days the first trade or two won, the owner stopped. Those days made +$13,193.
- Skipping the first three trades every day also skips those wins, which is why "first 3 on sim" came out about even.

**Not a clock time.** Net by entry time:

| Entry time | Trades | Net $ |
|---|---|---|
| 9:30–9:45 | 42 | −316 (26% wins) |
| 9:45–11:00 | 422 | +5,592 |
| 11:00–1:00 pm | 34 | +1,547 |
| After 1:00 pm | 13 | −1,392 |

The 4th-and-later trades came only about 25 minutes later than the first three (median
10:29 vs 10:04).

**The market at the time** (medians, first three trades vs later):

| | First 3 | 4th on |
|---|---|---|
| Day's range so far | 16.75 pts | 23.25 pts |
| Distance from the day's high/low | 5.75 pts | 8.75 pts |
| Entry outside the 09:30–09:45 range | 46% | 63% |
| Run into the entry, last 15 min | 2.0 pts | 1.25 pts |

**The difference is the opening range.**

| | Trades | Win | Net $ | Points per contract |
|---|---|---|---|---|
| First 3 trades, inside the 09:30–09:45 range | 189 | 27% | −4,033 | −0.46 |
| First 3, fading a breakout of that range | 89 | 38% | +819 | +0.36 |
| First 3, with the breakout | 66 | 38% | +3,019 | +0.80 |
| Later trades, inside the range | 62 | 37% | +2,131 | +1.14 |
| Later trades, outside it | 107 | 43% | +3,374 | +0.9 to +1.15 |

- **Entries inside the 09:30–09:45 range lost in both halves** (−$663 and −$1,239). Outside it, both fading and going with the breakout made money.
- Outside beats inside by +0.13 to +1.42 points per contract (95% range, resampling days).
- **Skipping entries inside the opening range:** 262 trades, +$7,211 instead of +$5,309, profit factor 1.38 instead of 1.13. Positive in both halves.
- "Wait for the first breakout" does not do the same job. The first break comes early (median 9:48), and later returns inside the range still lose early in the day.

**The bot shows none of this.**
- Its level-test entries win about 23–26% in every hour and whatever the test number.
- Inside vs outside the opening range: 27% vs 21% in 2024 ES, 25% vs 25% in 2026 MES.
- The pattern belongs to the owner's own choices. The bot only trades the coded read, which has no edge at any time of day.

## Caveats

- The read is my translation of the owner's description. Every threshold above is a proposal, and the owner's eye may weigh things differently.
- 23 days and 221 tests is still small. Differences under about 10 points are noise.
- The exits are fixed at 2 and 4 points. The owner's own exits were managed (average win 4.9 points).
- Sections 2–3: the 20 busiest days were also the losing days, and that book was MES.

## Possible next steps (each needs the owner's OK)

- The owner checks the charts, ideally next to their own Bookmap recordings of the same days if they have them, and says where my read differs from theirs.
- The ES setting of the absorption add-on, and the size that makes a "line" on the owner's screen.
