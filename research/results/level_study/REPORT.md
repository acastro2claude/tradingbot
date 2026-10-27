# Level study (build plan step 1): results

Run on 27 Oct 2026. MES, 1-minute bars for every contract, Mar 2025 to Oct 2026.
Main period: 276 sessions, 8 Sep 2025 to 23 Oct 2026. Separate early check: 108 sessions, Apr to Aug 2025.
Data cost: $9.11 in total ($3.37 of it was a lost download, see `data-spend.md`). About $115.89 credit left.

## Bottom line

1. **POC clusters did not stall or reverse price more than other prices.**
   - At a level, price went 6 points back away before trading 3 points through about 1 time in 3. That's what pure chance gives for a 6-for-3 bounce, and the same as any other price.
   - Other prices tested in the same hour held slightly more often than levels did.
   - Price did not pause longer at levels either.
2. **Strong levels (3 or more POCs) may hold a little better, but that isn't proven.** They are rare: at the 2-point grouping, 205 tests in 34 of the 276 sessions. The result also swings from +13 to −2 points depending on the grouping width.
3. **Low-volume areas were not crossed faster.**
   - Thin zones behaved like average zones.
   - The heaviest zones slowed price by about 20%, and prices that hadn't traded at all in the window were crossed about 10% faster.
   - Both effects disappear when a zone is compared with other zones in the same hour. They reflect busy versus quiet hours, not the zones themselves.
4. **The window question is not settled.** The 1-month profile showed slightly bigger differences. But a shorter window has more contrast just because it covers less time, while longer windows blend into one bell-shaped profile with fewer thin spots. So this test can't pick a window.

**What it means:** in this data the levels on their own carry no edge. Any edge has to come from the order flow read at the level (absorption). That is how the strategy is already built, and step 2 tests it directly.

## What was measured

- **Levels (STRATEGY.md 1a):**
  - POCs of the last 20 sessions, grouped when they sit within a set width. The width is still TBD, so 0.5, 1, 1.5, 2 and 3 points were all tested.
  - 2 or more POCs make a level; 3 or more make a strong level.
  - Older contracts are shifted by the roll gap (your decision). The measured gaps were 50 to 67 points, matching the calendar spread's own price to within 0.2 points.
- **A test:** price reaches the price after having been at least 5 points away from it.
- **Held:**
  - Price moved 6 points back away before trading 3 points through, within 4 hours. The 3 points match the bot's stop buffer.
  - Pure chance gives 33.3% for that (3 out of 9). 4 and 10 points were also checked.
- **Comparison group:** "other prices" are every price at least 2 points from any of the 20 POCs. Each level test is compared with other-price tests within 30 minutes of it in the same session, so the market conditions match.
- **Pause:** the time until price gets 3 points away on either side.
- **Zones (questions 2 and 3):**
  - Prices were cut into 1-, 2- and 4-point zones. Each time price entered a zone, we recorded whether it went through or turned back, and how long it took.
  - Each zone is rated by its volume in the long-term profile, compared with the average zone within 25 points.
- **Ranges in brackets** are 95% ranges, found by resampling whole sessions. A range that includes 0 means "no clear difference".

## Question 1: do POC clusters stall price?

Main period, held = 6 points back before 3 points through:

| POCs grouped within | Level | Tests | Sessions | Held | Other prices, same hour | Difference (95% range) |
|---|---|---|---|---|---|---|
| 0.5 pt | 2+ POCs | 443 | 54 | 35.4% | 35.8% | −0.3 pts (−4.8 to +4.0) |
| 1 pt | 2+ POCs | 747 | 73 | 34.5% | 36.7% | −2.0 pts (−6.1 to +1.7) |
| 1 pt | 3+ POCs | 38 | 7 | 42.1% | 34.9% | +9.5 pts (−2.9 to +19.8) |
| 1.5 pt | 2+ POCs | 1,105 | 97 | 33.5% | 37.4% | −3.8 pts (−7.4 to −0.2) |
| 1.5 pt | 3+ POCs | 43 | 8 | 46.5% | 35.4% | +13.4 pts (+1.6 to +26.1) |
| 2 pt | 2+ POCs | 1,600 | 130 | 33.6% | 37.7% | −4.0 pts (−6.8 to −1.3) |
| 2 pt | 3+ POCs | 205 | 34 | 37.1% | 34.8% | +3.1 pts (−4.5 to +10.0) |
| 3 pt | 2+ POCs | 2,006 | 140 | 32.9% | 38.9% | −5.9 pts (−8.4 to −3.2) |
| 3 pt | 3+ POCs | 301 | 41 | 35.5% | 37.7% | −1.6 pts (−7.4 to +3.9) |

No 3+ POC group formed at the 0.5-point width.

**Odds at other distances (2-point grouping):**

| Hold distance (stop side 3 pts) | Chance | Other prices | 2+ POC levels | 3+ POC levels |
|---|---|---|---|---|
| 4 pts | 42.9% | 42.8% | 44.0% | 47.6% |
| 6 pts | 33.3% | 33.6% | 33.6% | 37.1% |
| 10 pts | 23.1% | 23.0% | 23.0% | 28.2% |

- **Trading terms:** a blind 6-point-target, 3-point-stop trade at a 2+ POC level on 10 MES wins about $300 a third of the time and loses $150 two thirds of the time. That breaks even before costs and loses after slippage and commissions.
- **Splits (2-point grouping, 2+ POCs, versus same-hour other prices):**
  - First test of the session: −2.1 pts (−8.5 to +4.5).
  - Later tests: −4.3 (−7.4 to −1.4).
  - Support (price came down to it): −6.7 (−10.6 to −3.1). Resistance: −1.2 (−5.2 to +2.7).
  - Regular hours (09:30–16:00): −1.3 (−4.6 to +2.2). Overnight: −8.0 (−13.1 to −2.9).
- **Early period (Apr–Aug 2025), 2+ POCs:** between −4.9 and +2.3 points across all widths, and every range includes 0. Same answer.
- **Pause:**
  - Raw numbers: price stayed within 3 points of a level for 1.5 minutes (median), against 1.0 minute for other prices.
  - But levels mostly get tested in slower hours. Ranked against other prices tested in the same hour, levels score 0.50 (0.49 to 0.52), where 0.50 means exactly typical.
- **Front-running check:** prices 1–4 ticks in front of a POC held 33% of the time, against 34–35% further away. Price doesn't turn early either.
- **Example 1 (3 Apr 2025):** the nearest POC from the previous 20 sessions was 5586.75, 46 points above the 5540 reaction. That trade was not at a POC cluster; the reaction came at the heavy resting orders on the heatmap.
  - One coincidence: the 13 Mar POC was exactly 5540.00 in March-contract prices (5592.25 after the roll shift). A chart that doesn't adjust for the roll would have shown a POC right there.

## Question 2: does price cross low-volume areas faster?

1-month profile, 2-point zones. "Through" = price left out the far side instead of turning back.

| Zone volume vs nearby average | Visits | Went through | Typical time to cross | vs same day | vs same hour (95% range) |
|---|---|---|---|---|---|
| no volume yet (new prices) | 40,707 | 35.6% | 16 s | +3.1 pts, 0.89× time | −0.0 pts (−0.3 to +0.2), 1.00× (0.99 to 1.01) |
| under 0.3× | 14,059 | 30.7% | 21 s | +0.1 pts, 1.00× | +0.4 pts (−0.2 to +1.0), 0.99× (0.96 to 1.02) |
| 0.3–0.5× | 22,919 | 32.3% | 19 s | +0.7 pts, 0.95× | −0.1 pts (−0.6 to +0.5), 1.01× (0.99 to 1.03) |
| 0.5–0.75× | 81,034 | 35.1% | 18 s | +0.6 pts, 0.98× | +0.0 pts (−0.2 to +0.3), 1.01× (1.00 to 1.02) |
| 0.75–1× | 170,807 | 35.3% | 18 s | −0.0 pts, 1.00× | −0.0 pts (−0.2 to +0.2), 1.00× (0.99 to 1.00) |
| 1–1.25× | 166,765 | 34.3% | 18 s | −0.7 pts, 1.02× | −0.1 pts (−0.3 to +0.1), 1.00× (1.00 to 1.01) |
| 1.25–1.5× | 69,972 | 32.4% | 20 s | −0.7 pts, 1.04× | +0.2 pts (−0.1 to +0.5), 1.00× (0.99 to 1.01) |
| 1.5–2× | 29,298 | 30.0% | 23 s | −0.5 pts, 1.02× | +0.0 pts (−0.4 to +0.5), 0.99× (0.97 to 1.01) |
| over 2× | 6,413 | 25.2% | 29 s | −2.8 pts, 1.19× | −0.2 pts (−1.3 to +0.9), 0.99× (0.95 to 1.03) |

How to read the comparison columns:

- **Raw numbers:** the thinnest and the heaviest zones both turn price back more often than average zones.
- **Compared with the rest of the same day:**
  - The heaviest zones are crossed 2.8 points less often and take 19% longer.
  - Prices not traded at all in the last month are crossed a bit more often and 11% faster.
  - Thin zones that have traded are average.
- **Compared with the same hour:** nothing is left. Price sits in heavy areas during slow, rotating hours. The zone's volume adds nothing beyond what the current hour's pace already shows.
- **Proof the method can detect an effect:** rating zones by the same day's volume (hindsight) does show differences: −2.9 points and 0.87× time for thin zones, +1.3 points and 1.04× for heavy zones. The long-term profiles show nothing close to that.
- **Other widths:** 1-point and 4-point zones give the same picture (`q2_crossing_by_volume.csv`).

## Question 3: which long-term window works best?

Lowest-volume fifth of zones against the highest-volume fifth, 2-point zones, raw numbers:

| Window | Lowest fifth went through | Highest fifth went through | Difference (95% range) | Crossing time, lowest vs highest |
|---|---|---|---|---|
| 1 month | 34.2% | 31.5% | +2.7 pts (+1.3 to +4.2) | 0.87× (0.81 to 0.93) |
| 3 months | 33.9% | 32.3% | +1.6 pts (+0.1 to +3.3) | 0.92× (0.86 to 0.99) |
| 6 months | 33.8% | 32.6% | +1.2 pts (−0.4 to +2.7) | 0.93× (0.86 to 1.00) |

- **Head to head, same sessions:**
  - 1 month beats 3 months narrowly: crossing time 0.95× (0.90 to 0.99), through-rate gap +0.9 pts (−0.1 to +1.9).
  - Against 6 months it is about the same: 0.95× (0.89 to 1.00).
  - 3 and 6 months are identical.
- **Within the same hour,** none of the windows predicts anything (previous section).
- **Why this doesn't pick a window (owner's point):**
  - A longer window blends into a flatter, bell-shaped profile with fewer thin spots. The thinnest fifth of zones is under 0.74× the nearby average for 1 month, against 0.80× for 6 months. Zones under 0.3× make up 2.3% of the 1-month profile and 1.5% of the 6-month one.
  - So the 1-month profile was always going to separate zones a bit more.
  - The 1–4 point zones also suit a short window's detail. A 6-month profile's real features (for example a wide gap between two big balance areas) are larger.
  - A fair comparison would scale the zone size to each window.

## Data check: 1-minute bars against real trades

The 4 days with full trade data:

| Session | Contract | POC from trades | POC from bars | Profile match | Same outcome for the first test of each price |
|---|---|---|---|---|---|
| 3 Apr 2025 | MESM5 | 5482.00 | 5481.50 | 0.98 | 96% (600 prices) |
| 21 Oct 2025 | MESZ5 | 6780.00 | 6779.50 | 0.99 | 100% (125) |
| 24 Feb 2026 | MESH6 | 6903.50 | 6889.25 | 0.98 | 100% (338) |
| 21 Jul 2026 | MESU6 | 7547.00 | 7547.25 | 0.98 | 100% (325) |

- **Volume:** matches exactly every day.
- **24 Feb:** the day had two nearly equal peaks. In trades it was 13,044 contracts at 6903.50 against 12,308 at 6889.75; the bars flipped the order by 14 contracts.
- **How often that happens:** about 13% of days have a second peak within 2% of the POC, so roughly 1 POC in 10 could sit on the other peak. That would weaken a real level effect a little. It could not erase one.

## Level source B: recent uncovered POCs (the owner's method, STRATEGY.md 1e)

Added after the owner described how they trade:
- **Levels:** the POCs of the last 5 sessions.
- **Dropped:** any POC that the next session's value area covered. That removed 30% of the POCs that were 2 to 5 days old.
- **Scoring:** the same as question 1, with held = 6 points back before 3 through.

Main period:

| Group | Tests | Sessions | Held | Other prices, same hour | Difference (95% range) |
|---|---|---|---|---|---|
| Uncovered recent POCs (owner's levels) | 3,661 | 232 | 33.0% | 35.9% | −3.2 pts (−5.1 to −1.3) |
| … yesterday's POC | 1,636 | 206 | 33.2% | 35.5% | −2.7 pts (−5.4 to +0.2) |
| … 2 to 5 days old | 2,025 | 163 | 32.8% | 36.2% | −3.6 pts (−6.0 to −1.0) |
| … first test of the session | 454 | 231 | 32.8% | 30.4% | +2.1 pts (−2.7 to +6.8) |
| Covered recent POCs (dropped) | 1,225 | 109 | 32.6% | 37.5% | −4.8 pts (−8.1 to −1.5) |

- **Other hold distances and the early period:** 4- and 10-point hold distances, and the early period (Apr–Aug 2025), give the same picture (`recent_pocs_hold_rates.csv`).
- **Reading:** on their own, the owner's levels behave like any other price, and dropping covered POCs makes little difference.
- **What this does not test:** this checks the levels alone. The owner reads them together with supply and demand zones (1b, still TBD) and the absorption at the level (step 2). The targets and the checkpoint are not tested yet either.

## Supply and demand overlay (STRATEGY.md 1b, 1e and level strength)

- **How the zones were built:** from the owner's description, using the proposed defaults in 1b.
  - Chop: 3 or more candles through one common price.
  - Sharp move: a candle at least 2× the last chop candle, closing outside the chop.
  - A zone dies when a candle closes beyond its far side, after more than 60 minutes in a row inside it, or after a month.
  - No lookahead: a zone only counts after its sharp-move candle has closed, and until it dies.
- **What a match means:** a POC lines up with a zone when it sits inside it. A support test (price came down) needs a demand zone; a resistance test needs a supply zone.

| Zone timeframe | Zones | Per session | Median height | Median life | Ended by: blown past / chopped in / a month old |
|---|---|---|---|---|---|
| 15 min | 1,532 | 3.7 | 10.5 pts | 5.8 h | 63% / 31% / 5% |
| 30 min | 848 | 2.1 | 15.3 pts | 10.0 h | 47% / 46% / 7% |
| 60 min | 435 | 1.1 | 20.0 pts | 13.9 h | 30% / 63% / 6% |

Main period, held = 6 points back before 3 through. The comparison group is other prices with no POC within 2 points and outside every live zone, tested in the same hour:

| Zones from | Group | Tests | Held | Other prices, same hour | Difference (95% range) |
|---|---|---|---|---|---|
| 15 min | Owner's POC inside a matching zone | 619 | 34.1% | 37.3% | −3.5 pts (−8.1 to +1.1) |
| 15 min | Owner's POC, no zone | 3,042 | 32.7% | 36.3% | −4.1 pts (−6.1 to −2.1) |
| 15 min | Zone near edge, no POC nearby | 2,270 | 34.5% | 33.6% | +1.4 pts (−0.9 to +3.6) |
| 30 min | Owner's POC inside a matching zone | 515 | 34.6% | 38.8% | −5.6 pts (−10.5 to −0.5) |
| 30 min | Owner's POC, no zone | 3,146 | 32.7% | 36.4% | −3.9 pts (−6.0 to −1.9) |
| 30 min | Zone near edge, no POC nearby | 1,373 | 35.0% | 32.8% | +1.9 pts (−1.0 to +4.8) |
| 60 min | Owner's POC inside a matching zone | 233 | 36.1% | 33.8% | +1.4 pts (−6.5 to +9.4) |
| 60 min | Owner's POC, no zone | 3,428 | 32.8% | 36.1% | −3.6 pts (−5.5 to −1.6) |
| 60 min | Zone near edge, no POC nearby | 768 | 32.7% | 32.1% | +0.6 pts (−3.1 to +4.1) |

- **POC plus zone:** didn't hold better than other prices on any timeframe.
- **Zones on their own:** the near edges came out 0.6 to 1.9 points better than other prices. That isn't reliable.
- **The zones are tall** (median 10 to 20 points), probably taller than the zones the owner draws by eye.
  - Keeping only zones up to 6 points tall leaves too few to judge: 284 zones on the 15-minute chart and 53 tests of a POC inside one.
  - The near edges of those tight 15-minute zones held 38.3% against 35.2% for other prices: +5.1 pts (−2.0 to +11.5) over 201 tests. That's a hint, not proof (`sd_overlay_hold_rates.csv`).
- **Next check:** the rules may not match how the owner draws zones. A few example zones from the owner's charts would show whether the detector finds the same ones.

## Proposals (your call)

1. **Keep POC levels as "where to look", but don't expect them to do the work.** In step 2, compare absorption signals at levels with absorption signals anywhere. That shows whether the levels add anything once the order flow is included.
2. **POC cluster width stays TBD.** No width stood out. I suggest testing 1 point and 2 points in step 2.
3. **Long-term window:** keep the 3-month default for now. This test can't pick a window (see question 3). If the window matters later, retest with zone sizes scaled to each window.
4. **Optional, $3.90:** two more years of 1-minute bars (Mar 2023 to Feb 2025) would more than double the strong-level sample. That is the only open question here more data could settle cheaply.
5. **Low-volume areas as "fast move" zones:** the data doesn't support it. I suggest not leaning on them for targets or speed assumptions until step 2 shows otherwise.

## Choices made for the study (not set in STRATEGY.md)

- **Sessions left out:**
  - Holiday and early-close sessions (last trade before 16:00 ET, 15 sessions) are left out of both the POCs and the scoring.
  - Days Databento marks as degraded (9 sessions) are not scored; their POCs are still used.
- **Contract per session:** the one with the most volume that session. The switch happens on the Monday of roll week.
- **POC ties:** the price nearest the session's volume-weighted average price wins. Value area: start at the POC and add whichever side has more volume.
- **Grouping:** the window holding the most POCs is grouped first; ties go to the narrowest window. Each group's level is the average of its POCs, rounded to a tick.
- **Order inside a 1-minute bar:** open, low, high, close on up bars; open, high, low, close on down bars.
- **Definitions:** the 5-point move needed before a new test, the 4-hour limit, and the 25-point neighbourhood for rating zones are all mine. They are set in `research/settings.toml`.

## Files

| File | What it holds |
|---|---|
| `sessions.csv` | Each session: contract, volume, POC, value area, roll gap, flags |
| `daily_profiles_1min_bars.parquet` | Volume at each price for every session, in that contract's prices. Lets later steps rebuild levels without downloading again. |
| `levels_by_session.csv.gz` | The POC groups available on each scored session, for each width |
| `q1_hold_rates.csv`, `q1_splits.csv`, `q1_pause.csv` | Question 1 |
| `q2_crossing_by_volume.csv`, `q2_detail_by_volume.csv`, `q2_volume_class_edges.csv`, `q2_hindsight_check.csv` | Question 2 |
| `q3_window_comparison.csv`, `q3_window_pairs.csv` | Question 3 |
| `recent_pocs_hold_rates.csv` | Level source B: recent uncovered POCs |
| `sd_overlay_hold_rates.csv`, `sd_zone_stats.csv` | Supply and demand overlay |
| `check_bars_vs_trades.csv` | The data check |

## How to rerun

From the repo root (the downloads cost about $5.75, so ask first):

```
python -m research.download_level_study_data   # bars + 4 trade days, checks prices first
python -m research.level_study                 # about 2.5 minutes
python -m research.recent_poc_study            # owner's levels (1e); needs level_study first
python -m research.supply_demand_study         # supply and demand overlay; needs level_study first
python -m research.check_bars_vs_trades
python -m pytest tests
```
