# Step 2: absorption and the full setup (first results)

Run on 27 Oct 2026. MES trades, regular hours 09:30–16:00 ET, 130 sessions from 27 Apr to 23 Oct 2026.

## Bottom line

1. **Absorption bursts point the right way slightly more often than random entries, but not enough to trade on their own.**
   - Shorting right after a purple burst (6-point target, 3-point stop) worked 33% of the time, against 28% for random shorts in the same half hour.
   - But 33% is only break-even before costs on a 6/3 bracket.
2. **The full setup loses money in this period on every version tested.**
   - About 75–85% of trades hit the stop 3 points past the level, half of them within a couple of minutes.
   - The best versions come out around break-even: −$13 to −$14 a trade on 10 MES, and both ranges include profit and loss.
3. **The rules replay Example 1 correctly.** Given its 5540 level:
   - early entry: short 5539.75 at 09:33:39, +$1,266
   - retest entry: short 5536.75 at 09:39:45, +$995 (the owner's own figure was about $990)

   The trade logic does what the spec says. What the data doesn't show is an edge from the rules as written.

## How the absorption check was measured

- **Bursts:** 100+ contracts at one price by one aggressor side within 0.01 s, about 106 a session.
- **Purple → short, green → long.** Each was scored as a trade:
  - Entered at the price a market order would get (a short at the bid, a long at the ask).
  - Worked = 6 points in favour before 3 points against; failed = the 3-point stop first.
- **Comparison:** random entries in the same session within 30 minutes, filled and scored the same way.
  - An earlier version scored from the last trade price. That handed bursts a free tick, so it was discarded.

| Entry | Worked | Random, same time | Difference (95% range) |
|---|---|---|---|
| Short right after a purple burst | 33.2% | 27.7% | +5.4 pts (+3.7 to +7.4) |
| Long right after a green burst | 30.5% | 26.7% | +3.8 pts (+1.6 to +6.0) |
| Purple, after price held within 2 ticks for 30 s | 33.2% | 27.0% | +6.2 pts (+4.2 to +8.2) |
| Any burst at a 15-min supply/demand zone | 33.7% | 22.5% | +11.2 pts (+6.2 to +16.1) |

Full table: `absorption_check_results.csv`.

## The full setup (STRATEGY.md sections 2–4)

- **Setup:** a purple (short) or green (long) burst within 2 points of a level, with mostly the same colour of absorption at the level in the last 5 minutes.
- **Entry:** a market order, plus slippage.
- **Stop:** 3 points past the level; risk at most 7 points.
- **First target (9 contracts):** the top of the last 1-minute chop before the move into the level. It must be at least 2× the stop distance.
- **Runner:** the low of that chop. Its stop moves to 6 points from entry after the first target.
- **Day rules:** one trade a day. Fed-day blackout. No entries after 15:30; flat at 15:59 (the data ends at 16:00).
- **Costs:** 1 tick of slippage on market orders and $1.24 per contract round trip in commission.

| Variant | Levels | Trades | First target hit | Stopped | Avg per trade (95% range) | Total |
|---|---|---|---|---|---|---|
| Original (early entry) | your levels (POC or zone) | 102 | 21% | 78% | −$49 (−$105 to +$15) | −$5,009 |
| Original | no level (the burst price is the level) | 126 | 21% | 79% | −$23 (−$77 to +$37) | −$2,940 |
| Original | 15-min S/D zones | 74 | 15% | 85% | −$78 | −$5,774 |
| Absorption only in front of the level | your levels | 90 | 19% | 80% | −$48 | −$4,290 |
| No entries before 09:45 | your levels | 99 | 19% | 80% | −$31 (−$99 to +$44) | −$3,063 |
| No entries before 10:00 | your levels | 95 | 17% | 82% | −$40 | −$3,824 |
| Bursts of 150+ | your levels | 65 | 15% | 83% | −$32 | −$2,072 |
| 2+ bursts at the level | no level | 125 | 18% | 82% | −$13 (−$74 to +$60) | −$1,606 |
| Retest entry | no level | 110 | 27% | 73% | −$14 (−$96 to +$72) | −$1,498 |
| Retest entry | your levels | 43 | 19% | 77% | −$26 (−$171 to +$132) | −$1,132 |
| All filters, early entry | your levels | 39 | 13% | 85% | −$65 | −$2,554 |
| All filters, retest entry | your levels | 16 | 19% | 75% | −$46 | −$728 |

"All filters" means: in front of the level, no entries before 10:00, bursts of 150+. Every trade is in `backtest_trades.csv`, and all versions, including 2 ticks of slippage, are in `backtest_summary.csv`.

**What stands out:**
- **The retest entry has the best first-target rate (27%).** It also has the widest stops (about 5 points).
- **Stops are hit fast.** The median stopped trade lasted 1.4 minutes in the original version. A stop 3 points past the level sits inside normal noise, especially in the first hour, where most trades happen because bursts cluster at the open.
- **Levels don't help yet.** "No level" (trade the absorption price itself) does as well as or better than POCs and zones. This matches step 1.

## Choices made here (proposed, TBD in STRATEGY.md)

- **At the level:** a burst within 2 points of it. "Mostly seller absorption" means seller-burst contracts ≥ buyer-burst contracts at the level over the last 5 minutes.
- **Retest:**
  - price pulls at least 6 points away after the first test (4 points caught 14-second wiggles at the open)
  - comes back within 2 points of the level within 60 minutes without reaching the stop
  - shows less volume at the level and a CVD lower high (higher low for a long) than the first test
  - entry once price turns 1 point off the retest high
- **Commission:** $1.24 per contract round trip. This depends on the broker or prop firm.
- **Time limits:** the data ends at 16:00, so no entries after 15:30 and flat at 15:59. The spec says 16:30 and 16:50; Topstep would need 16:10.
- **Not tested yet:** the quiet-day adjustment, volume bubbles, the order book, and the 8 / 1 / 1 split.

## What the owner's read has that this doesn't

1. **Levels from the order book.** Example 1's level was a heatmap line of resting orders, not a POC or zone. That's step 3.
2. **Selection.** The detector fires about 100 bursts a day; the owner acts on a few. The context the eye uses (the size of the move into the level, the heatmap, delta) isn't fully captured.
3. **Stop placement.** 3 points past the level gets hit about 80% of the time in this data.
