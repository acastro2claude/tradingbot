# MES POC-Cluster Order Flow Strategy: Spec (draft)

Status: draft. Items marked **TBD** still need a decision.

## Market and platform

- **Instrument traded:** MES (Micro E-mini S&P 500), **10 contracts per trade**.
  - Tick size 0.25. Each contract is worth $1.25 per tick or $5 per point, so 10 contracts are $50 per point, the same as 1 ES.
  - MNQ may come later.
- **TBD:** compute levels and volume profiles from ES instead of MES. ES is the main market and MES follows it, so the ES profile may show the big players better. Absorption alerts stay on MES to match the Bookmap settings.
- **Platforms:** Bookmap for reading order flow, NinjaTrader for execution.
- **Data feed:** none active yet. Rithmic is planned for live trading.
- **Live trading:** a NinjaScript strategy running in NinjaTrader 8, using the Rithmic feed for trades and the order book.
- **Research and backtesting:** Python, using historical CME tick data.
- **Prop firm (TBD):** the owner is considering a prop firm, for example Topstep. Topstep's published rules (checked 27 Oct 2026):
  - Bots are allowed through the TopstepX API. High-frequency trading is banned, along with exploiting simulated fills and latency arbitrage. This strategy (at most a few trades a day, held for minutes) is not high-frequency.
  - **New accounts can only use TopstepX.** NinjaTrader and Tradovate appear to remain only on older accounts.
  - **TBD:** with Topstep, the live bot would connect to the TopstepX API instead of running as a NinjaScript strategy. The other option is a prop firm that supports NinjaTrader with Rithmic.
  - **Flat by 3:10 PM CT (16:10 ET)** every day. No new positions after 3:08 PM CT, and nothing held overnight.
    - **TBD:** if Topstep is used, the end-of-day times in section 4 must move earlier.
  - **To confirm with the firm:**
    - whether running from a VPS or VPN is allowed (a third-party guide says it isn't)
    - whether the data feed includes every trade with its side and the market depth
    - the loss limits the bot must stay inside

## Core idea

Price tends to stall where a lot of volume traded before and to move quickly through prices where little traded.
When price reaches a strong high-volume level, the order flow there decides whether to trade:

- **Absorption** (heavy aggressive orders that fail to push price through): the level holds, so trade the reversal.
- **No absorption** (price cuts through on heavy volume): the level breaks. No trade for now (see Later ideas).

## 1. Levels

### 1a. Daily POC clusters (level source A)

Source A and source B (1e, the owner's current method) are both tested. The data decides.


- Find the point of control (POC, the price with the most volume) for each of the last **N = 20** sessions.
- Group POCs that sit within **W ticks** of each other. The default W is **TBD**.
- 2 or more POCs in a group make a **level**; 3 or more make a **strong level**.
- **Session:** the full Globex session, 18:00–17:00 ET (23 hours). The Sunday 18:00 open counts as the start of Monday's session.
- **Contract rolls:** each session's profile uses one contract. POCs from the old contract are shifted by the roll gap (the price difference between the new and old contract on roll day), like a back-adjusted chart, so the 20-session lookback and the long-term profile keep working across rolls.

### 1b. Supply and demand zones

How the owner draws them:

- **What makes one:** an area where price hangs around (chops), then a sharp move away in one direction.
  - A sharp move up leaves a **demand zone** below.
  - A sharp move down leaves a **supply zone** above.
- **Sharp move:** there's no set size. Roughly, it's at least double the size of the last candle in the chop.
- **Chart:** zones hold up better when drawn on a higher-timeframe chart.
- **Lookback:** zones from up to about a month back.
- **When a zone stops counting:**
  - price blows past it, or
  - price starts chopping in it.
  - Price can come back, hang around the zone for a little while and then retest. But if it hangs around too long (over about an hour), the zone is probably no longer valid.

Proposed defaults for the bot (**TBD**, for the owner to confirm):

- **Higher timeframe:** test 15-, 30- and 60-minute candles.
- **The chop:** 3 or more candles in a row that all trade through one common price.
- **Where the zone is drawn (owner's choice):** the body of the last chop candle before the sharp move, from its open to its close. That gives zones of about 1.5 points on 15-minute candles, 2 points on 30-minute and 3 points on 60-minute.
  - The owner asked to use bigger zones if they work better. The level study compared four sizes (about 1.5 to 20 points) and none clearly worked better, so this drawing stays (see the level study report).
- **Sharp move:** the next candle's range is at least 2× the last chop candle's range, and it closes beyond the chop.
- **Blows past:** a candle on the zone's timeframe closes beyond the far side of the zone.
- **Chops in it:** more than 60 minutes in a row with every 1-minute close inside the zone.
- **Lines up with a POC:** the POC sits inside the zone.

### 1c. Long-term volume profile

- Combined volume by price over a longer window. Test **1, 3 and 6 months** and keep whichever best predicts fast moves.
- **Low-volume areas:** price is expected to move through these quickly.
- The long-term POC and value area (the range holding about 70% of the volume) serve as extra levels and targets.

### 1d. Resting-order lines

- In example 1, the reaction happened at a heatmap line of heavy resting orders (5540). After that day's gap down, the previous days' POCs were likely far above.
- **Owner (Oct 2026):** resting orders mean nothing unless they sit at or around a level already marked (POCs, supply and demand zones). Away from marked levels they are usually pulled before price gets there. So a line does not make a level on its own; it confirms a marked level.
  - The order book data agrees: on the owner's busiest days, big lines anywhere near price did not hold price on their own (`research/results/journal/REPORT.md`).
- **TBD:** how big an order must be to count as a line (Bookmap shades it dark relative to what's on screen), and how close to the marked level it must sit.

### 1e. Recent uncovered POCs (level source B: how the owner trades now)

- **Levels:** the POCs of the last **5** sessions. Trade the reversal against these.
- **Covered POCs don't count:** drop a POC if the next session's value area (the range holding about 70% of that session's volume) includes it.
  - Yesterday's POC always counts, because today's value area isn't finished.
- **Supply and demand overlay:** these POCs are read together with the supply and demand zones (1b). The more they line up, the better the trade (see Level strength).
- **Targets:** an older POC outside the last couple of days' trading can be the target. Price tends to be drawn back to prices that traded heavily before.
  - **TBD:** how far back target POCs can come from, and whether a target POC must also be uncovered.
- **Checkpoint on the way:** if a price between the entry and the target has traded more volume, traders may treat it as fair value.
  - Watch whether price breaks out past it. If it doesn't, the trade may chop there or come back to the entry.
  - This is not a reason to skip the trade or the target. It is a point to manage the trade.
  - **TBD:** how to spot that price (for example, another day's POC between entry and target, or a heavy price in a combined profile of recent days), and what the bot does if price stalls there (for example, tighten the stop or take partial profit).

### Level strength

A level ranks higher when the sources agree. Examples:

- A recent uncovered POC (1e) that lines up with a supply or demand zone. This is how the owner trades now.
- A POC cluster that overlaps a supply or demand zone.
- A cluster that sits next to a long-term low-volume area.

## 2. Entries: absorption at the level

The bot copies the read currently done by eye in Bookmap. That read uses volume bubbles, the Absorption Indicator add-on, delta and the bid/ask orders on the book.

### Signals to measure

- **Absorption alerts** (rebuilds the Bookmap Absorption Indicator add-on):
  - Current add-on setting: mark the chart when **100 or more contracts trade within 0.01 seconds** (on MES).
  - Purple markers mean seller absorption; green markers mean buyer absorption.
  - **Settled by the data (Example 1, Oct 2026):** purple means aggressive buying that sellers absorbed. The markers match bursts of aggressive buys traded at a single price within 0.01 seconds.
  - **Still to confirm:** whether a marker adds up repeated bursts at the same price.
  - The add-on only flags the burst of volume. The bot adds the absorption check itself: after the burst, price does not trade more than **X ticks** past that price within **Y seconds**.
- **Volume bubbles:** unusually large traded volume at one price in a short time. Size cutoff: **TBD** (start from Bookmap's settings).
- **Delta and CVD:** delta is aggressive buy volume minus aggressive sell volume. CVD (cumulative volume delta) is the running total of delta through the session. The bot compares the CVD highs (or lows) at the first test of a level and at the retest.
- **Volume at the level on each test:** how much traded at and near the level the first time price reached it, compared with the retest.
- **Order book:** the size of resting bids and asks at and near the level, and whether they refill after being hit.

### The read in the owner's words (Oct 2026)

Example: a long at a demand zone that has previous days' POCs inside it (a near-perfect setup). Mirror it for a short.

1. The heatmap shows a line of large resting bids (dark) at the marked level.
2. Price comes down to test it. Watch whether the bids get eaten, or whether even more resting bids join the book at the level.
3. As the bids are being eaten, the volume delta starts to skew toward buyers. That adds conviction that price may reverse up.
4. More conviction again with buyer absorption markers at those prices and more resting bids coming in to hold price.
5. Then look for the long entry.

**From the owner's answers on 12 blind review charts (Oct 2026):**
- **Wait while the other side is still in control.** For a long, that means delta is still making new lows or sellers are still the aggressors (buyers, for a short). "Too soon to tell."
- **Absorption:** a spike in aggressive buying (selling) at the level while price fails to push past it → short (long). Aggression without progress is absorption. Aggression with progress means that side is in control.
- **Weaker retest:** price comes back to a level with less aggressive volume pushing into it than on the last attempt, and more resting orders on the far side → enter.
- **Divergence in a range:** price at the previous high of the chop while delta makes a lower high → short toward the bottom of the chop (mirror for a long).
- **Zone direction:** short at supply zones, long at demand zones. No long while price keeps failing below a supply zone.
- **No trade** in chop with no clear movement.
- **Targets:** the next POC, the far side of the chop, or about 3/4 of the way across today's value area (the 70% volume node).
- **Continuation trades:** the owner also takes a retest of a chop zone in the direction of the move already made (delta rising, little aggression either way). This is not part of the bot's reversal setup. **TBD:** whether to add it.

- In 2024 the owner most likely watched the ES heatmap in Bookmap, even when trading MES.
- The journal replay shows the entry usually comes once price has already started to turn (median 0.5 points in the trade's direction in the last 5 seconds).

### Setup (example: short after price rises into a level)

1. Price rises into a level from section 1.
2. Absorption alerts appear at the level, for example sellers absorbing.
3. Price may hang around the level and produce more alerts on both sides. Keep a running count, with sizes, of buyer and seller absorption at the level.
   - Working assumption: mostly seller absorption points down, and mostly buyer absorption points up.
   - **TBD:** confirm this assumption, and decide how lopsided the balance must be.
4. Confirm with big volume bubbles at the level, the delta, and the resting orders on the book.
5. **Entry.** There are two versions, to be compared in the backtest:
   - **Early entry (what the user does by hand):** short at the level once seller absorption shows up, before any retest. In example 1, that's 5536–5538.
   - **Retest entry:** wait for price to pull away and come back to the level. Short when the retest is weaker than the first test and price turns down:
     - Less volume trades at the level than on the first test, which means traders aren't interested in that price any more.
     - CVD makes a lower high than at the first test.
   - After an early entry, a weak retest confirms the trade. It could also be a point to add to the position (see Later ideas).
6. **Target:** price returns to where the market wants to trade, meaning the nearest high-volume area below, such as the session POC. Section 3 has the full exit plan.

Mirror the steps for a long after price falls into a level.

- **TBD:** how far price must pull away for a retest to count, how much lower the retest volume must be, and the exact retest entry trigger (for example, price trading a few ticks below the retest high).
- All thresholds will be set from the add-on settings, example trades and the backtest.

### Example trades (test cases)

**Example 1 (short): MES Jun25, 3 Apr 2025, about 09:30–09:47 ET.** The values are read from a Bookmap screenshot, so they are approximate.

- Price rallies from about 5507 at 09:30 to about 5540 by 09:33.
- The heatmap shows heavy resting orders at 5540 and 5550 before price gets there.
- Seller absorption markers appear around 09:33–09:35: **118** at about 5540, **212** at about 5535.75 and **109** at about 5532.75.
- Price chops between about 5525 and 5540.
- **Retest around 09:40:** price pushes back to about 5538 and fails. Less volume trades at the level than on the first test, and CVD makes a lower high. This is the short signal.
- Price falls to about 5515 by 09:47.
- **User's trade:** short at 5536–5538 (early entry), stop at 5543, close 90% at 5515, runner targets at 5505 then 5500, and the runner's stop moved to 5530 after the first target. Risk is about 6 points against about 22 points to the first target (about 3.7 to 1).
- With 10 MES, the trade risks about $300. The 9 contracts closed at the first target make about $990, before commissions.
- April 3, 2025 was a very high-volume day (the tariff selloff), so normal settings apply.
- **Test:** rebuilding the indicator over this window must reproduce these three markers.
  - **Result from Databento trades** (aggressive buys at one price within 0.01 s):
    - 120 at 5540.00 (09:33:39), against the screenshot's 118.
    - 112 at 5532.50 (09:34:44), against 109.
    - 102 + 110 = 212 at 5535.50 (09:34:32 and 09:34:48), against 212. This one matches only if the indicator adds up bursts at the same price.

**Wanted:** an example trade from a quiet, low-volume day.

## 3. Exits

These rules follow how the user manages trades by hand. Example 1 prices are in brackets. The point distances are for a normal-volume day; see "Adjusting for the day's volume" below.

1. **Initial stop:** 3 points beyond the level being traded [5540 + 3 = 5543].
2. **First target:** close about **90%** of the position where the move into the level started [5515, where the rally began].
   - **Defined (owner, Oct 2026):** the top of the last chop before the sharp move into the level (for a long, its bottom). On 1-minute candles: 3 or more candles through one common price, then a candle at least 2× the last chop candle's range that closes beyond the chop. The target is that chop's high (low for a long), wicks included. [Example 1: 09:27–09:29 chopped at 5506.75–5515, and 09:30 broke out, so the target is 5515.]
   - **Minimum reward (owner):** take the trade only if the first target is at least **2×** as far as the stop.
3. **Runner:** the last contract targets the next low beyond that (high, for a long), such as the session's earlier low [5505].
   - **Defined (owner, Oct 2026):** the lowest price from the start of that chop up to the entry (highest, for a long). [Example 1: 5506.75.]
4. **After the first target:** move the runner's stop to lock in 5 points of profit plus 1 point of buffer, which is 6 points from entry [5536 − 6 = 5530].

- **Contract split with 10 MES:** 9 contracts close at the first target, and 1 contract runs to the next target.
  - The backtest will compare this with an 8 / 1 / 1 split, where a second runner targets the level after that [5500]. The data decides.
- **Alternative target (section 1e):** an older POC outside the last couple of days' trading. Any heavier-volume price on the way is a checkpoint to manage the trade. The backtest compares this with the targets above.
- **Backtest costs:** assume a tick or two of slippage on entries and stops, plus commissions.

### Adjusting for the day's volume

Low-volume days produce smaller moves, so the bot takes less risk on them.

- **Measure:** relative volume, meaning today's volume so far divided by the average volume up to the same time of day over the last 20 sessions. Comparing at the same time of day keeps the quiet overnight hours from looking like a low-volume day.
- **Normal day** (relative volume around 1 or higher; exact cutoff **TBD**): use the normal settings above.
- **Low-volume day** (below the cutoff): keep the same number of contracts, but move the stop and targets closer.
  - Starting rule: multiply every distance by a factor. That covers the stop buffer, the first target, the runner targets and the locked-in profit. Starting guess: **0.6**.
  - The backtest will compare a fixed factor with one that shrinks further as relative volume drops.
- The backtest will test the cutoff and the factor.

## 4. Risk limits

- **Contracts per trade:** 10 MES on every trade. On quiet days the stop and targets move closer instead of the size changing.
- **Loss per trade (normal day):** the stop sits 3 points past the level, and entries so far have been 2–4 points inside the level. A full loss is therefore 5–7 points, which is **$250–$350** on 10 MES. Slippage and commissions add roughly $10–$40.
- **Trades per day:** **1**, win or loss. This is an adjustable setting, to be raised once results show whether the strategy works. With one trade a day, the most the bot should lose in a day is one full loss: about $260–$390 including costs.
- **Fast markets:** during news or sharp moves, a stop can fill well past its price. A single loss can then be bigger than these numbers.
- **Maximum entry distance (TBD):** skip the trade if price is already too far from the level, for example more than 4 points away, so the risk never goes past about 7 points.
- **Daily loss limit:** a backup dollar limit for when more than one trade a day is allowed. Proposed: $800, about two full losses.
- **Protective orders:** send the stop and the first target as soon as the entry fills, so the trade stays protected if the bot or the computer goes down. Check that the stop is held by the broker or the exchange, not only by NinjaTrader on the computer.
- **End of day:** close any open trade at **16:50 ET**, 10 minutes before the 17:00 ET session close. No new entries after **16:30 ET**. On holiday early-close days, the same rules apply relative to the early close.
- **Major news:** no new entries from **15 minutes before to 30 minutes after** a major release. Close an open trade **5 minutes before** the release, because stops can slip badly when the numbers come out.
  - Default events: CPI, the jobs report (nonfarm payrolls) and Fed rate decisions. On Fed days, the block lasts until the press conference ends.
  - The bot reads release dates and times from a calendar file built from the official schedules (BLS and the Federal Reserve). The backtest skips the same windows.

## 5. Bot settings

Every number below is an adjustable setting. The defaults come from this spec.

| Setting | Default | Notes |
|---|---|---|
| Instrument | MES | MNQ may come later |
| Contracts per trade | 10 | |
| Max trades per day | 1 | Win or loss. Raise once results are in. |
| Daily loss limit | $800 | Only matters when more than 1 trade a day is allowed |
| Session | 18:00–17:00 ET | Full Globex session |
| No new entries after | 16:30 ET | |
| Close open trade at | 16:50 ET | 10 minutes before the session close |
| News events | CPI, jobs report, Fed decisions | |
| News blackout for entries | 15 min before to 30 min after | Fed days: until the press conference ends |
| Close open trade before news | 5 min before the release | |
| POC lookback | 20 sessions | |
| POC cluster width | TBD | |
| POCs needed for a level | 2 (3 = strong) | |
| Recent POC lookback (1e) | 5 sessions | |
| Covered POC rule (1e) | Next session's value area | A POC inside it no longer counts |
| Value area | 70% of the session's volume | |
| Supply/demand chart timeframe | TBD | Study tests 15, 30 and 60 minutes |
| Supply/demand lookback | 1 month | |
| Chop before the move | 3+ candles through one common price | Proposed, TBD |
| Zone drawn from | Body of the last chop candle | Open to close of the candle before the sharp move |
| Sharp move size | 2× the last chop candle | |
| Zone invalid after chopping in it | 60 minutes | Also invalid when a candle closes beyond the far side |
| Long-term profile window | 3 months | Backtest compares 1, 3 and 6 months |
| Absorption burst | 100 contracts in 0.01 s | Matches the Bookmap add-on |
| Absorption hold check | TBD | Price stays within X ticks for Y seconds |
| Entry mode | Early | Alternative: retest entry. Backtest compares. |
| Max entry distance from level | 4 points | |
| Stop buffer past the level | 3 points | |
| First target | Top of the last chop before the move into the level | Closes 9 of 10 contracts |
| Minimum reward | First target at least 2× the stop distance | |
| Runner | 1 contract to the low of that chop and breakout | Alternative: 8 / 1 / 1 split |
| Runner stop after first target | 6 points from entry | 5 points locked in plus 1 point of buffer |
| Quiet-day cutoff | TBD (relative volume around 1) | |
| Quiet-day factor | 0.6 | Multiplies the stop and target distances |

## Later ideas (on hold)

- **Breakout and retest:**
  1. Price breaks through a level.
  2. Price comes back to retest the level, and the level holds. Dips back through it get bought (or sold) immediately.
  3. Enter in the breakout direction.

  This is not part of the first version.
- **Add to the position on a weak retest:** after an early entry, add when the retest confirms the trade (less volume at the level, CVD lower high).

## Build plan

1. **Level study (Python):** using historical trades, answer three questions:
   - Does price stall at POC clusters more often than at random prices?
   - Does it cross low-volume gaps faster than other prices?
   - Which long-term window works best?

   **First results (Oct 2026, 1-minute bars, Sep 2025–Oct 2026):** POC clusters held no more often than other prices tested in the same hour. Low-volume areas were not crossed faster once the time of day is matched. The window comparison was inconclusive: shorter windows show more contrast simply because they cover less time. Strong levels (3+ POCs) were too rare to judge. Recent uncovered POCs (1e) on their own also held no better than other prices, and neither did those POCs inside a supply or demand zone (zones drawn from the last chop candle's body, 1b), though that combination is rare (about 100–150 tests in 14 months). The absorption read is not tested yet. Details and proposals: `research/results/level_study/REPORT.md`.
2. **Strategy backtest:** rebuild the absorption alerts, volume bubbles and delta from trade data that carries the buy/sell side. Then test the setup in section 2.

   **First results (Oct 2026, trades Apr–Oct 2026, regular hours):** absorption bursts point the right way slightly more often than random entries at the same time. The full setup (early or retest entry, stop 3 points past the level, the owner's targets, costs) still loses on every version tested. The best versions come out around break-even, and about 75–85% of trades are stopped out. The rules replay Example 1 correctly. Details: `research/results/step2/REPORT.md`.
3. **Add the order book:** test the resting-order signals with historical book data.

   **First look (Oct 2026, full MES book for the owner's 20 busiest journal days in 2024–2025, 163 trades):** the owner's entries came once price had already started their way. Compared with random moments that had the same move, the orders in the path of the trade were being pulled faster than they were added more often (51% vs 40%). Big resting lines next to the entry were rare, and on their own they did not hold price. None of the book readings tested separated winners from losers. These 20 days were also the owner's losing days (26% win rate, −$6,015), and the journal trades are mostly ES while this book is MES. Details: `research/results/journal/REPORT.md`.

   **ES book, five whole weeks of 2024 (Oct 2026, 23 days):** the owner's read was coded as a bot (marked level, line at the level, eaten and stacking, delta, absorption, entry on a 1-point turn; 2-point stop, 4-point target, 1 tick slippage). Over 221 level tests, no version reached break-even (about 40% wins needed). POC tests did best at 28% wins. The full read won 20% (25 tests). Over the same days, the owner made 126 trades and lost $1,410. The bot's versions lost between $749 and $2,932. The owner traded only 13 of the 230 tests. The thresholds are proposals awaiting the owner's check on the charts.

   **Exit styles on the same tests (Oct 2026):** none made the read profitable. 3-point stop / 7-point target: −$79 per trade. Stop past the test: −$54 to −$67. The section 3 targets with the stop 3 points past the level came closest at −$30 per trade (+$295 for the bot over 40 trades, carried by a few big runners). Losses are almost all quick stop-outs (median 1.2 minutes for a 2-point stop), and 56% of tests later traded through the target. But a wider stop doesn't fix it: at every stop size these entries won 4–9 points less often than random entries on the same days. So the entry is the problem, not the exit: price tends to push further through the level before reversing. Adding the read's conditions made every exit worse, so the coded read likely differs from the owner's. **Blind review (12 charts):** the owner took 4 and skipped 8. The skips avoided the trades that ran 6–21 points against. Two of the four takes reached the owner's target. Turning the owner's reasons into rules: the bot's first-turn entry nearly always comes while the other side is still in control. Waiting for that to fade (patient entry) did not help (22% wins). The clearest signal is the weaker retest: 34% winners with a 2-point stop and 4-point target when the push into the level was weaker than last time, vs 15% when it wasn't (47 and 40 tests). Still about −$19 per trade, and borderline on this sample. **Checked on 2026 MES trades (126 sessions, 1,359 level tests):** the weaker retest won 29% vs 25% for other retests (+4 points, 95% range −3 to +11), down from 19 points in 2024. Level tests as a whole did no better than random entries (25% vs 27%). Nothing came near break-even. So the mechanical level-test entries found so far have no edge. The owner's discretionary read (the skips in the blind review) is still unmeasured on a sample big enough to judge. Next: a blind review, where the owner marks take or skip on charts that stop at the entry. Orders that trail price (for example 150-lot orders kept 3.5 points from price) are real orders, not a data error. The owner says they can count as resting orders.
4. **Live bot:** a NinjaScript strategy, tested in this order:
   - Market Replay (past days replayed, order book included)
   - Sim account
   - Live trading with minimum size

## Data

- **Needed first, for the backtest:** historical CME tick data. Recommended source: Databento.
  - Start with trades. They include the aggressor side, which is enough for levels, delta, volume bubbles and absorption alerts.
  - Add order book history for build step 3.
- **Needed last, for the live bot:** a futures broker account with Rithmic and a real-time CME data subscription that includes market depth, connected to NinjaTrader and Bookmap.
- **Other historical option:** Rithmic's own history. It may be limited, and Rithmic may need to approve API access for a custom program.
- **Bookmap recordings:** the owner has saved Bookmap recordings (.bmf) from past trading. They contain the full order book, but only Bookmap can open them: Bookmap offers no open file format for its feed data (owner, Oct 2026). The order book history comes from Databento's MBO data instead, which carries the same CME order-by-order feed Bookmap draws its heatmap from.
