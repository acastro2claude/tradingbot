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

**The owner's definition (Oct 2026).** The owner marks zones the way their supply and demand course teaches (slides and charts shared in Oct 2026). Supply is the same idea in reverse.

- **What makes one:** a **base** (one or a few candles where price pauses, a balanced market), then a strong move away (an imbalance). Large orders were left unfilled in the base, and price tends to come back to it "to pick up the rest of the orders".
- **The four patterns.** The move into the base gives the name; the move away gives the side:
  - Rally-base-rally (continuation, forms in uptrends) and drop-base-rally (reversal) leave a **demand zone**. Buy the first pullback into it.
  - Drop-base-drop (continuation, forms in downtrends) and rally-base-drop (reversal) leave a **supply zone**. Sell the first rally into it.
- **Basing candle (owner, Oct 2026):** the candle right before the move away. It is red before a rally (demand) and green before a drop (supply). If the candle right before the rally is green, there is no basing candle and no zone.
- **How the owner draws a zone (Oct 2026):** from the body of the basing candle to the wick. For supply, from the bottom of the green basing candle's body up to the upper wick (of the basing candle or the drop candle). For demand, from the top of the red basing candle's body down to the low wick. All zones are drawn this way.
  - Example: the 22 Aug 2024 4-hour supply zone is 5645.50–5665.25 (Sep contract). 5645.50 is where the green basing candle opened (the candle before it closed there). 5665.25 is the drop candle's upper wick.
  - Confirmed by the owner (Oct 2026): the zone starts at the basing candle's **open** on both sides. That is the bottom of a green candle's body for supply and the top of a red candle's body for demand, so the whole body is inside the zone. The 15 Aug 2024 demand zone is 5532.50–5550.75 on the owner's chart (5471.75–5490.00 Sep contract).
- **Strong move away:** there's no set size. Roughly, it's at least double the size of the basing candle. If the basing candle's wicks are big and the next candle doesn't move much past it, it is not a zone (owner on the 19 Aug 2024 1-hour zone at 5593).
- **Not every candle that moves away makes a zone (owner, Oct 2026).** When price is chopping in a range, those candles are chop, not zones.
- **Fresh zones:** the best zones are fresh and untested: price has not been back since it left. They hold the most waiting orders. Look from right to left on the chart to find them.
- **Timeframes (owner, Oct 2026):** 4-hour, 2-hour, 1-hour, 30-minute and 15-minute. A zone that shows on a higher timeframe is usually stronger. No daily zones: they are too big. The course also trades a higher-timeframe zone from a lower-timeframe chart (for example a 1-hour supply zone on a 15-minute chart). The owner's example below is a 4-hour zone.
- **No overlapping zones (owner, Oct 2026):** avoid them. If a zone can be marked on both a lower-timeframe and a higher-timeframe chart, the higher-timeframe zone is the one that counts.
- **Lookback:** zones from up to about a month back. Untested zones from earlier days and weeks count (owner, Oct 2026). Looking only at zones made within the day misses most of them.
- **Retests (owner, Oct 2026):** a zone is still valid on retests, but like a level, the more retests, the less valid it gets. At 4 or more retests it is probably on its way out, unless the order book shows heavy pressure from the zone's side.
  - **TBD:** what counts as "heavy pressure" on the order book.
  - The bot counts the first return as test 1, so "on its way out" starts at test 4 (proposed reading).
- **Stop (owner, Oct 2026):** the normal stop rules (section 3), even when the zone is big. The course puts the stop just past the zone; the owner does not.
- **Target (course):** take profits at the opposite zone (a long from demand takes profit at supply).
- **When a zone stops counting:**
  - price blows past it, or
  - price starts chopping in it.
  - Price can come back, hang around the zone for a little while and then retest. But if it hangs around too long (over about an hour), the zone is probably no longer valid.

**The owner's example (Oct 2026): a new 4-hour demand zone on ES, 14–15 Aug 2024.**

- Price rallied in small 4-hour candles on 14 Aug. It paused with a small red candle (Wed 14 Aug, 22:00–02:00 Pacific), then rallied 45 and 30 points in the next two candles.
- The zone top is the open of that red candle: 5550.75 on the owner's thinkorswim chart, 5490.00 in the Sep 2024 contract. The owner's chart is back-adjusted, 60.75 points above the Sep contract. It shows Pacific time, with 4-hour candles starting at midnight Chicago time.
- The bot's rules (below) find this zone at 5471.75–5490.00 (Sep contract). It is a test case in `tests/test_zones.py`.
- First return: Thu 5 Sep 2024 at 12:16 ET, to exactly 5490.00. Price bounced 37 points in about 1.5 hours. It came back overnight and bounced 20 points again, then broke the zone on the 6 Sep jobs report.

**The owner's verdicts on the bot's 4-hour zones, Aug–Sep 2024 (Oct 2026):**

- 5368 demand: no basing candle (the base was green candles under a rally). It did bounce on a retest, "so maybe there is some merit".
- 5431 supply: no basing candle.
- The zones at the top of the late-August range: chop, not zones.
- 5636 supply: the only one that is valid, because it held multiple retests. It should be drawn the owner's way: 5645.50–5665.25.

**The owner's verdicts on the review page, week of 12–23 Aug 2024 (Oct 2026):**

- 4-hour 5565 demand (19 Aug): no basing candle. The candle before the rally was green.
- 2-hour 5630 supply (20 Aug): no base, then drop. The candle before the drop was red.
- 1-hour 5593 demand (19 Aug): the basing candle's wick is too big, and the next candle didn't move enough (only 1.3× the basing candle).
- 30-minute 5377 demand (13 Aug): no real rally (only 1.4× the basing candle).
- 15-minute 5377 demand (13 Aug): no basing candle, no rally.
- The other 30-minute and 15-minute zones looked good (a quick look, not zone by zone).

**How the bot draws them.** The owner reviewed the charts and said the rest look good (Oct 2026). Values marked proposed stay open to change. Code: `research/zones.py`. Settings: `[zones]`.

- **Candles:** the owner's five timeframes, starting at midnight Chicago time like the owner's thinkorswim charts. A candle never spans two sessions.
- **Basing candle:** the candle right before the move away: red before a rally, green before a drop (the owner's rule).
  - A doji (body at most 10% of its high-low range) also counts (proposed: 3 doji zones the owner glanced at and approved).
- **Move away:** one candle that:
  - is at least **1.5×** the basing candle's size, high to low. The owner said "roughly double"; 1.5× drops the two zones the owner said didn't move enough (1.3×, 1.4×) and keeps the 30m/15m zones they approved, several of which were 1.7–1.9× (proposed),
  - has a range at least 1.5× the average of the last 20 candles (the owner's example: 2.7×), and
  - closes beyond the basing candle.
- **Not chop (proposed):** price must move at least **1.5× the zone's height** away from it before it first comes back; otherwise there is no zone. The bot only knows the zone from that moment.
  - Under the earlier drawing, this rule separated the owner's verdicts: 5636 moved 1.8× its height away, and the chop zone at 5594 only 1.0×. Drawn the owner's way, 5636 moves 3.2×. 5594 no longer has a basing candle.
- **Edges (the owner's drawing):**
  - The near edge is the basing candle's open (owner, confirmed for both sides). For a doji, it is the body edge price comes back to first.
  - The far edge is the wick: for demand, the lowest low of the basing and move-away candles; for supply, the highest high.
  - These rules reproduce all 9 of the owner's verdicts so far (2 valid, 7 rejected). They are test cases in `tests/test_zones.py`.
- **Tests:** price trades back to the near edge. A new test needs price to move away again by the zone's height (at least 5 points) first (proposed). From the 4th test, the zone is "on its way out".
- **Ends:** whichever comes first:
  - **Broken:** a candle on the zone's timeframe closes beyond the far edge.
  - **Chopped in (proposed):** price closes inside the zone on every 1-minute bar for 4 candles of the zone's timeframe, counting trading minutes only. That is the owner's hour on 15-minute zones and 16 trading hours on 4-hour zones.
    - Drawn the owner's way, the 5636 zone is 5645.50–5665.25. It no longer ends this way: it lives through its retests until a 4-hour candle closes above it on 19 Sep 2024.
  - **Expired:** 31 days after it formed.
- **Overlaps (the owner's rule):** two zones overlap when their price ranges share a tick while both still count.
  - The higher timeframe wins. On the same timeframe, the earlier zone wins.
  - A zone that would form over a winning zone is not marked.
  - A lower-timeframe zone stops counting when a higher-timeframe zone forms over it. The lower one usually shows up first, while the higher one's base is still forming.
- **Invalid zones (owner, replay page, Oct 2026):** the owner called 56 of 104 replayed zones invalid. Not yet in the bot's rules:
  - Price already blew past the zone, often both ways ("previous day").
  - Demand with price trading below it, or supply with price above it.
  - Price hanging inside the zone too long.
  - Only what happens **after** the zone forms counts (owner, Oct 2026). The page's side chart had shaded each zone across the days before it existed (fixed). 31 of the 56 invalid calls were judged on price action from before the zone formed. On the fixed chart the owner traded 11 of those 31 (+$47 a trade), and only 1 was still called invalid.
- **On 2024 ES data:** about 16 zones a week across the five timeframes, after overlaps (40 before the basing-candle and chop rules).
  - Per week: 1.3 four-hour, 1.2 two-hour, 1.6 one-hour, 4.6 thirty-minute and 7 fifteen-minute.
  - Median height in points: 10.5 (4-hour), 10 (2-hour), 5.5 (1-hour), 3.75 (30-minute), 2.25 (15-minute).
  - A zone gets 2 tests on average; 1 in 6 reaches 4 or more. Most zones end by breaking (89%).
- **For the owner to check:** the interactive review page `research/results/journal/review/zones/index.html` (published as an artifact). Mark each zone valid or noise and note the missed ones. Static charts: `research/results/journal/zone_charts/course_zones_*.png`.

**Rejected (Oct 2026):** the owner would not use any of the bot's earlier zones, which did not match how the owner marks zones. Those used 15-minute candles and a chop of 3 or more candles; the zone was the body of the last chop candle, and it was dead after an hour of chop. The code is kept in `research/supply_demand.py` for the earlier studies.

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

**Owner (Oct 2026): supply and demand zones come first. POCs are supplementary.** Volume by price still matters a lot: it shows how participants are trading and what price they see as fair. But a POC never comes before a good supply or demand zone. So:

- **The trade level is a good supply or demand zone (1b).**
- **POCs and the volume profile add weight** when they line up with the zone, and help pick targets and checkpoints. They also help judge where participants see fair value.
- **A POC on its own is not a trade level.** The earlier level sources (POC clusters, recent uncovered POCs on their own) are now context only.

Examples, strongest first:

- A good supply or demand zone with a recent uncovered POC (1e) inside it.
- A good supply or demand zone on its own.
- A good zone next to a long-term low-volume area.

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

**From the replay page (24 trades and 77 skips, Oct 2026):**
- Every trade was in the zone's direction.
- Reasons to take a trade:
  - CVD makes a higher low at demand (a lower high at supply) as price retests the zone.
  - Absorption as price hits the zone, then the other side comes in.
  - A weaker second push into the zone.
  - Resting orders holding the line.
- Reasons to skip:
  - The zone is invalid (section 1b).
  - The open ("we don't trade the open").
  - News spikes.
  - A "moving train" with nobody stopping it.
  - Chop at the zone.
  - Absorption without the other side coming in.

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
- **Topstep account (owner, Oct 2026):** the owner trades a Topstep account. Hitting its loss limit loses the account, and starting over costs money, so staying off the limit comes first.
  - **Maximum Loss Limit:** $2,000 (50K account), $3,000 (100K), $4,500 (150K). The owner: it trails the best end-of-day balance by the same amount at all times, "so we cannot let up just because we are winning."
  - **Daily Loss Limit:** $1,000 (50K); hitting it ends the trading day.
  - **TBD:** the owner's account size.
  - The owner wants at most **2 trades a day** to stay off the limit.
  - **Tested on the owner's journal (REPORT.md section 20):** with the owner's entries from 10:00 at 10 MES, a 50K Combine passes 70% of the time with no extra rule, 39% with the 2-trade cap. At 5 MES: 92% with no extra rule, at about 85 trading days instead of 32.
    - A run of losing days hits a trailing limit, not one bad day. Topstep's −$1,000 day already caps a single day.
    - The owner decides the cap and the size.
- **Protective orders:** send the stop and the first target as soon as the entry fills, so the trade stays protected if the bot or the computer goes down. Check that the stop is held by the broker or the exchange, not only by NinjaTrader on the computer.
- **End of day:** close any open trade at **16:50 ET**, 10 minutes before the 17:00 ET session close. No new entries after **16:30 ET**. On holiday early-close days, the same rules apply relative to the early close.
- **Major news:** no new entries from **15 minutes before to 30 minutes after** a major release. Close an open trade **5 minutes before** the release, because stops can slip badly when the numbers come out.
  - **Owner (Oct 2026, blind review):** also no trade in the several minutes after any scheduled release, including the 10:00 ET reports, when volume jumps.
  - **Which reports (owner, 30 Oct 2026):**
    - CPI
    - employment and jobs reports
    - PMI
    - job openings
    - Fed decisions (whole day off, below)
  - **My reading of that list (proposed):**
    - Jobs: nonfarm payrolls (08:30 ET) and ADP (08:15), plus weekly jobless claims (08:30 Thursdays).
    - PMI: the owner means "US Manufacturing PMI": S&P Global's, at 09:45, both the early (flash) and the final reading. ISM Manufacturing comes out at 10:00 on the same morning as the final, so it falls in the same block. Services PMIs are not included (proposed).
    - Job openings: JOLTS (10:00).
  - **Owner (30 Oct 2026):** skip only around the time of the news, not the whole day. Trade again once volume settles down, usually 30 minutes to an hour after the release; "then price tends to act rationally again."
    - **Proposed (TBD):** after the first 30 minutes, entries resume once the last 5 minutes' volume is back under 1.5× normal for that time of day (the average of the last 20 sessions), and at the latest 60 minutes after the release.
  - **Fed decision days: no trading at all, the whole session** (owner, 30 Oct 2026: "those days are crazy"). In the owner's journal from Q2 2024 on, the 13 trades on Fed days came out about even (−$17), against +$19 a trade on other days.
  - **Journal check (Oct 2026):** skipping entries in the 10 minutes after a news volume spike (08:30, 10:00 or 14:00 ET minute at 3× or more the 10 minutes before) would have added +$1,309 to the owner's 2024–25 journal, in both halves.
  - **Calendar:** the bot reads release dates and times from a calendar file built from the official schedules (BLS, ISM, ADP and the Federal Reserve). The backtest skips the same windows.
    - `research/news_calendar.csv` so far has only CPI, the jobs report and Fed dates for Apr–Oct 2026.
    - The other reports, and 2024–2025, need adding before the next all-days test.
- **Open:** no trades too soon after the 09:30 ET open (owner, Oct 2026 review). **TBD:** how long to wait.

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
| No entries before | 10:00 ET | Owner: "we don't trade the open". The owner skipped all 19 replay clips before 10:00. 10:00 proposed, TBD |
| News events | CPI, employment and jobs reports, PMI, job openings, Fed decisions | Owner (30 Oct 2026). Read as: payrolls, ADP, weekly jobless claims, US Manufacturing PMI (S&P Global, 09:45, flash and final) with ISM Manufacturing (10:00), JOLTS (proposed). Only the time around a release, not the whole day |
| News blackout for entries | 15 min before to 30–60 min after | Owner: until volume settles, usually 30 min to an hour. Proposed: after 30 min, resume once 5-minute volume is under 1.5× normal for that time of day; at most 60 min |
| Fed decision days | No trading at all | Owner: "those days are crazy" |
| Close open trade before news | 5 min before the release | |
| POC lookback | 20 sessions | |
| POC cluster width | TBD | |
| POCs needed for a level | 2 (3 = strong) | |
| Recent POC lookback (1e) | 5 sessions | |
| Covered POC rule (1e) | Next session's value area | A POC inside it no longer counts |
| Value area | 70% of the session's volume | |
| Supply/demand timeframes (1b) | 4h, 2h, 1h, 30m, 15m | Owner. No daily (too big). Candles start at midnight Chicago time |
| Overlapping zones | The higher timeframe wins | Owner. Same timeframe: the earlier zone wins |
| Supply/demand lookback | 1 month | An untested zone expires after 31 days |
| Base | 1–3 candles through one common price | Proposed, TBD |
| Strong move away | Body at least 2× every base candle's body | Plus a range at least 1.5× the last 20 candles' average (proposed, TBD) |
| Zone edges | Near edge = base bodies, far edge = base wicks | Course charts and the owner's example |
| Basing candle | The candle right before the move: red (demand) or green (supply) | Owner. A doji also counts (proposed) |
| Move-away size | At least 1.5× the basing candle, high to low | Owner said roughly double. 1.5× proposed |
| Zone drawn | Basing candle's body (its open) to the wick | Owner |
| Not chop | Price moves 1.5× the zone's height away before it first comes back | Proposed, TBD |
| Zone tests | Valid on retests; from the 4th test only with heavy order-book pressure | Owner. "Heavy pressure" TBD |
| Zone broken | A candle on its timeframe closes beyond the far edge | Proposed |
| Zone invalid after chopping in it | 4 candles of the zone's timeframe | Owner said about an hour (= 4 on 15m). Proposed, TBD for big zones |
| Stop at a zone | Normal stop rules (section 3) | Owner: even when the zone is big |
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

## How changes are tested

**Owner (Oct 2026):** "each day is different. If we keep comparing changes against the same days we are only trying to optimize for certain days, and not trying to generalize the strategy in ALL conditions."

- Judge every change on all the days with data, not on a few fixed weeks: every session from Feb 2024 to Oct 2026 with 1-minute candles (588 days with zone entries).
- Report results by year, by quarter and by type of day (trend or range, volatility, gap), so it shows whether a rule works in all conditions.
- Rules are built on 2024. 2025 and 2026 stay unseen until a rule is fixed, and are only used to check it.
- The order-book weeks (23 days of 2024) are for questions only the book can answer, never for picking rules.

## Build plan

1. **Level study (Python):** using historical trades, answer three questions:
   - Does price stall at POC clusters more often than at random prices?
   - Does it cross low-volume gaps faster than other prices?
   - Which long-term window works best?

   **First results (Oct 2026, 1-minute bars, Sep 2025–Oct 2026):** POC clusters held no more often than other prices tested in the same hour. Low-volume areas were not crossed faster once the time of day is matched. The window comparison was inconclusive: shorter windows show more contrast simply because they cover less time. Strong levels (3+ POCs) were too rare to judge. Recent uncovered POCs (1e) on their own also held no better than other prices, and neither did those POCs inside a supply or demand zone (zones drawn from the last chop candle's body, 1b), though that combination is rare (about 100–150 tests in 14 months). The absorption read is not tested yet. Details and proposals: `research/results/level_study/REPORT.md`.
2. **Strategy backtest:** rebuild the absorption alerts, volume bubbles and delta from trade data that carries the buy/sell side. Then test the setup in section 2.

   **First results (Oct 2026, trades Apr–Oct 2026, regular hours):** absorption bursts point the right way slightly more often than random entries at the same time. The full setup (early or retest entry, stop 3 points past the level, the owner's targets, costs) still loses on every version tested. The best versions come out around break-even, and about 75–85% of trades are stopped out. The rules replay Example 1 correctly. Details: `research/results/step2/REPORT.md`.
3. **Add the order book:** test the resting-order signals with historical book data.

   **Zones, the owner's way (Oct 2026, ES 1-minute bars, Feb 2024 – Mar 2025).**
   - The owner approved the zone charts.
   - Entered blindly at the zone edge with the normal 3-point stop, zone tests do no better than random entries (38% winners either way). The same holds as whole areas (stop past the far edge).
   - The owner's own trades near these zones made +$31 to +$40 per trade, against about break-even away from them. That is likely real but not proven.
   - So the zone says where to look, and the order-flow read decides. Next: the book read at zone tests on the 23 ES days on hand. Details: `research/results/journal/REPORT.md` section 14.
   - **Order book at the zones (23 ES days of 2024):** no book read separates bounces from breaks at zone tests (117 entries; 21% winners vs 27% random with 2/4). One exit, the 3-point stop past the zone edge with the STRATEGY.md targets, made +$2,835 on 27 trades, mostly from two trades. The same version lost $11,036 on MES in Apr–Oct 2026 (164 trades, 2 of 7 months up). Every exit lost in 2026.
   - Only 7 of the owner's 43 trades near zones on those days line up with the bot's zone tests. The owner enters at other moments than "price comes back to the edge after being 5 points away". Details: REPORT.md section 15.
   - **The owner's risk structure (Oct 2026):** "a 4 point stop loss, but let winners run to about 10-12 points depending on market conditions (mostly meaning delta is heavily continuing in our direction, and aggressive buyers/sellers are still moving the same direction)."
     - The owner's journal (Apr 2024 on): 34% winners, average win 5.8 points, average loss 2.3.
     - Tested on all days and on 2026 tick data, the zone bot still loses with 4/10, 4/12 and the delta-based 10-or-12 exit (24–25% winners, about 31% needed). Random entries with the same exits lose too: the exits multiply an edge in the entries but cannot create one. Details: REPORT.md section 17.
   - **Replay page (Oct 2026, 104 clips of zone tests, date hidden):**
     - The owner took 24 trades, all in the zone's direction: 38% winners, +$25 per trade with 4/10 (+$602), +$32 with the 10-or-12 hold.
     - The bot's own entry on the same clips: −$7 per trade. On the 75 clips the owner skipped: −$50. Random entries on the clips the owner took: −$22.
     - Most of the difference is which clips the owner takes. The owner's entry price was also about 1 point better than the bot's.
     - 24 trades don't prove it (95% range −$111 to +$161 per trade).
     - Most skips (56 of 77) were "invalid zone". Details: REPORT.md section 18.
     - **All 155 clips (second sitting, 30 Oct):** 59 trades, 31% winners, −$15 per trade.
       - The trades decided carefully, with notes, made +$30 per trade (38 trades). That includes +$47 on 11 zones first called invalid because of the chart bug.
       - The last 45 clips were decided quickly, without notes: −$98 per trade on 21 trades. Those clips were also hard for the bot's entry (10% winners).
       - Not proven either way.
   - **5-point vs 4-point stop and at most 2 trades a day (owner's test, Oct 2026):**
     - On the replay trades, the 5-point stop saved 3 trades that were stopped at −4 and then reached +10 (careful trades +$66 vs +$30 a trade).
     - On the owner's 371 real journal trades from Q2 2024, re-run with fixed brackets, 5/10 and 4/10 come out the same (−$8 a trade; +$4 vs −$1 from 10:00). The owner's own exits did better than any fixed bracket (+$22 a trade from 10:00).
     - At most 2 trades a day cut the profit in almost every version (the owner's own exits from 10:00: +$5,977 → +$1,720), because the later trades of the day were the better ones.
     - The owner decides the stop and the cap. Details: REPORT.md section 19.
   - **All days, Feb 2024 – Oct 2026 (588 sessions, 1-minute candles):** the zone bot matches random entries in every year, quarter and type of day. For example, 27% winners vs 28% random with 2/4, and only 2–4 of 33 months up. Details: REPORT.md section 16.

   **First look (Oct 2026, full MES book for the owner's 20 busiest journal days in 2024–2025, 163 trades):** the owner's entries came once price had already started their way. Compared with random moments that had the same move, the orders in the path of the trade were being pulled faster than they were added more often (51% vs 40%). Big resting lines next to the entry were rare, and on their own they did not hold price. None of the book readings tested separated winners from losers. These 20 days were also the owner's losing days (26% win rate, −$6,015), and the journal trades are mostly ES while this book is MES. Details: `research/results/journal/REPORT.md`.

   **ES book, five whole weeks of 2024 (Oct 2026, 23 days):** the owner's read was coded as a bot (marked level, line at the level, eaten and stacking, delta, absorption, entry on a 1-point turn; 2-point stop, 4-point target, 1 tick slippage). Over 221 level tests, no version reached break-even (about 40% wins needed). POC tests did best at 28% wins. The full read won 20% (25 tests). Over the same days, the owner made 126 trades and lost $1,410. The bot's versions lost between $749 and $2,932. The owner traded only 13 of the 230 tests. The thresholds are proposals awaiting the owner's check on the charts.

   **Exit styles on the same tests (Oct 2026):** none made the read profitable. 3-point stop / 7-point target: −$79 per trade. Stop past the test: −$54 to −$67. The section 3 targets with the stop 3 points past the level came closest at −$30 per trade (+$295 for the bot over 40 trades, carried by a few big runners). Losses are almost all quick stop-outs (median 1.2 minutes for a 2-point stop), and 56% of tests later traded through the target. But a wider stop doesn't fix it: at every stop size these entries won 4–9 points less often than random entries on the same days. So the entry is the problem, not the exit: price tends to push further through the level before reversing. Adding the read's conditions made every exit worse, so the coded read likely differs from the owner's. **Blind review (12 charts):** the owner took 4 and skipped 8. The skips avoided the trades that ran 6–21 points against. Two of the four takes reached the owner's target. Turning the owner's reasons into rules: the bot's first-turn entry nearly always comes while the other side is still in control. Waiting for that to fade (patient entry) did not help (22% wins). The clearest signal is the weaker retest: 34% winners with a 2-point stop and 4-point target when the push into the level was weaker than last time, vs 15% when it wasn't (47 and 40 tests). Still about −$19 per trade, and borderline on this sample. **Checked on 2026 MES trades (126 sessions, 1,359 level tests):** the weaker retest won 29% vs 25% for other retests (+4 points, 95% range −3 to +11), down from 19 points in 2024. Level tests as a whole did no better than random entries (25% vs 27%). Nothing came near break-even. So the mechanical level-test entries found so far have no edge. **Blind review, second round (40 charts on a review page, Oct 2026):** the owner took 19 and skipped 21. The takes did not do better than the skips: 16% vs 24% reached 4 points before 2 against, and 16% vs 29% reached the owner's own target with a 3-point stop. Across both rounds (52 charts) the difference is within chance (p ≈ 0.5). Caveats: static charts are not live Bookmap (the owner noted they can't see the book move), the entry moment and stop were the bot's, and in 2024 the owner used these levels only partly. So far neither the coded rules nor the static chart read shows an edge at these marked levels. **Day rules on the owner's journal (Oct 2026):** trade caps, stopping after losses and daily loss limits would all have cut the owner's profit (for example, at most 3 trades a day: +$5,309 → −$196). The owner's first ~3 trades of a day had no edge, and the later ones did (+$5,505 on 169 trades, in both halves). Taking the first three at half size or on sim kept the profit with lower drawdown. The section 5 cap of 1 trade a day would have left +$240 on this journal. Details: `research/results/journal/REPORT.md` section 9. **What made the later trades better (section 10):** not the clock. The losers were early entries inside the 09:30–09:45 range (189 trades, −$4,033). Skipping every entry inside that range would have made +$7,211 instead of +$5,309 (profit factor 1.38), in both halves. The bot's level tests show no such pattern (about 25% wins inside and outside the range, at every hour). **Proposed (owner's call):** no entries while price is inside the 09:30–09:45 range. **The journal over time (section 11):** the owner says early 2024 lacked discipline, and the data agrees. Q1 2024 lost $1,524 with quick scalps. From Q2 2024 on, every quarter was profitable (+$6,833 on 373 trades), and the average win grew from 2.8 to 7.6 points. In that period, trades near a marked level did better (+1.70 vs +0.24 points per contract, 48 trades), but that is not yet proven (95% range −0.24 to +3.48). Use Q2 2024 onward as the reference for the owner's method. **Disciplined period only (section 12):** the owner's two filters (no entries inside the 09:30–09:45 range, none in the 10 minutes after a news spike) turn +$6,833 into +$8,898 on 188 trades (profit factor 1.66), in both halves. The best bot version on the 2024 ES weeks of that period (+$832, STRATEGY.md exits) lost $18,436 over six months of 2026 MES. Every bot version built so far loses in both years. Next: a blind review, where the owner marks take or skip on charts that stop at the entry. Orders that trail price (for example 150-lot orders kept 3.5 points from price) are real orders, not a data error. The owner says they can count as resting orders.
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
