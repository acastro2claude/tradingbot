"""Why the learned bot takes the trades it takes: a reasoning log, and how consistent the owner's decisions are.

Owner (Nov 2026): "add logging to the bot ... I want to know why the bot is taking trades on skips,
and what its thought process and reasoning is ... Do you also find that my trades are consistent?"

1. Reasoning log. The same cross-validation as research.learn_read (5 folds by day; the model never
   saw the day it trades). For every entry the bot makes on a held-out clip, SHAP values (how much
   each input pushed the model toward "enter now") give its top reasons, written out in trading
   terms, next to the owner's decision and note on that clip. Then, across all entries: which
   reasons drive the trades the owner skipped compared with the ones the owner also took.
2. The owner's consistency:
   - clips of the same day and same side whose zone tests came within 2 minutes of each other
     (the same moment of the market at two nearby zones): do the decisions match?
   - the 5 most similar clips (by the tape at 90 s after the test, other days only): how often is
     the decision the same, compared with chance?
   - the reasons in the notes: how often a reason written on a take also appears on a skip
   - results by sitting
Writes research/results/journal/learn_explain_log.csv, learn_explain_reasons.csv,
owner_consistency.csv and bot_trade_log.md. Needs scikit-learn and shap.
Run:   python -m research.learn_explain
"""

from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd

import research.learn_read as lr
from research.bot_week_study import FLAT_ET, SLIP_TICKS, et
from research.entry_read import day_trades_df
from research.exits import bot_exit
from research.journal_study import OUT
from research.owner_rules import DayTrades
from research.replay_score import DECISIONS

TICK = lr.TICK


def phrase(f: str, v: float, d: int) -> str:
    us, them = ("buyers", "sellers") if d == 1 else ("sellers", "buyers")
    if f == "t_since_test":
        return f"{v:.0f} s after the zone test"
    if f == "dist_near":
        return f"price {v:+.2f} pts from the zone's edge (+ = back out of the zone)"
    if f == "depth":
        return f"the low went {v:.2f} pts into the zone"
    if f == "through_far":
        return f"the low was {v:+.2f} pts past the zone's far edge"
    if f == "bounce":
        return f"price {v:.2f} pts off the low"
    if f == "secs_since_low":
        return f"the low was {v:.0f} s ago"
    if f == "pushes":
        return f"{v:.0f} pushes into the zone so far"
    for pre, who in (("ours_", us), ("against_", them)):
        if f.startswith(pre):
            return f"{who}' aggressive volume over the last {f[len(pre):-1]} s: {v:.1f}x normal"
    if f.startswith("balance_"):
        return f"last {f[8:-1]} s: {100 * (1 + v) / 2:.0f}% of aggressive volume was {us}"
    if f == "cvd_since_test":
        return f"CVD since the test {v:+.1f} (in minutes of normal volume; + = {us})"
    if f == "cvd_since_low":
        return f"CVD since the low {v:+.1f} (+ = {us})"
    if f.startswith("range_"):
        return f"price range of the last {int(f[6:-1]) // 60} min: {v:.2f} pts"
    if f.startswith("volume_"):
        return f"volume of the last {int(f[7:-1]) // 60} min: {v:.1f}x normal"
    if f.startswith("move_in_"):
        return f"price came {v:+.2f} pts toward the zone in the {f[8:-1]} min before the test"
    if f.startswith("markers_ours_"):
        return f"{v:.0f} absorption markers of {them} absorbed (good for us) in the last {int(f[13:-1]) // 60} min"
    if f.startswith("markers_against_"):
        return f"{v:.0f} absorption markers against us in the last {int(f[16:-1]) // 60} min"
    if f == "zone_minutes":
        return f"{v:.0f}-minute zone"
    if f == "zone_test":
        return f"test {v:.0f} of the zone"
    if f == "zone_height":
        return f"zone {v:.2f} pts tall"
    if f == "minutes_after_open":
        m = int(v) + 570
        return f"time {m // 60:02d}:{m % 60:02d} ET"
    return f"{f} = {v:.2f}"


def cross_validate():
    import shap
    from sklearn.model_selection import GroupKFold
    data = lr.clip_rows()
    lr.FEATS = [c for c in data.columns if c not in ("label", "time", "price", "clip", "day", "market", "contract",
                                                     "direction", "owner_takes", "owner_entry_time", "owner_entry_price")]
    full = lr.all_points()
    clips = data.groupby("clip").agg(day=("day", "first"), owner=("owner_takes", "first")).reset_index()
    log, contrib = [], []
    for k, (tr_i, te_i) in enumerate(GroupKFold(n_splits=lr.L["folds"]).split(clips, groups=clips["day"])):
        tr_c, te_c = set(clips.loc[tr_i, "clip"]), set(clips.loc[te_i, "clip"])
        m = lr.fit(data[data["clip"].isin(tr_c)])
        tp = full[full["clip"].isin(tr_c)]
        thr = float(pd.Series(m.predict_proba(tp[lr.FEATS])[:, 1]).groupby(tp["clip"].to_numpy()).max().quantile(1 - clips.loc[tr_i, "owner"].mean()))
        fp = full[full["clip"].isin(te_c)].copy()
        fp["p"] = m.predict_proba(fp[lr.FEATS])[:, 1]
        ex = shap.TreeExplainer(m)
        for c, g in fp.groupby("clip"):
            kk = lr.first_entry(g["p"].to_numpy(), thr)
            info = lr.CLIP_INFO[c]
            row = dict(info, fold=k, thr=thr, max_p=float(g["p"].max()), bot_takes=kk is not None)
            if kk is not None:
                x = g.iloc[[kk]]
                sv = np.array(ex.shap_values(x[lr.FEATS]))
                sv = sv.reshape(-1)[-len(lr.FEATS):]
                order = np.argsort(-sv)
                row.update(bot_time=float(x["time"].iat[0]), bot_price=int(x["price"].iat[0]), p=float(x["p"].iat[0]),
                           reasons_for=" | ".join(phrase(lr.FEATS[i], float(x[lr.FEATS[i]].iat[0]), info["direction"]) for i in order[:4]),
                           reasons_against=" | ".join(phrase(lr.FEATS[i], float(x[lr.FEATS[i]].iat[0]), info["direction"]) for i in order[::-1][:2]))
                contrib.append(dict(clip_id=c, owner_takes=info["owner_takes"], **{f: v for f, v in zip(lr.FEATS, sv)},
                                    **{f"value_{f}": float(x[f].iat[0]) for f in lr.FEATS}))
            log.append(row)
    return pd.DataFrame(log), pd.DataFrame(contrib), full, clips


def results(log: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (market, contract, day), g in log.groupby(["market", "contract", "day"]):
        dt = DayTrades(trades=day_trades_df(market, contract, day))
        for r in g.itertuples():
            row = r._asdict()
            if r.bot_takes:
                row["bot_points"] = bot_exit(dt, r.bot_time, r.bot_price + r.direction * SLIP_TICKS, r.direction,
                                             et(day, FLAT_ET), move=False)[0] * TICK
            out.append(row)
    return pd.DataFrame(out).drop(columns=["Index"])


def consistency(full: pd.DataFrame, clips: pd.DataFrame) -> pd.DataFrame:
    info = pd.DataFrame(lr.CLIP_INFO.values())
    dec = pd.DataFrame([json.load(open(f)) for f in sorted(glob.glob(str(DECISIONS / "*.json")))]).set_index("clip_id")
    info["note"] = info["clip"].map(dec["note"]).fillna("")
    info["saved_at"] = info["clip"].map(dec["saved_at"])
    rows = []
    # a) the same moment of the market at two nearby zones
    pairs = []
    for day, g in info.groupby("day"):
        g = g.sort_values("test_time")
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                a, b = g.iloc[i], g.iloc[j]
                if b["test_time"] - a["test_time"] <= 120 and a["direction"] == b["direction"]:
                    pairs.append(a["owner_takes"] == b["owner_takes"])
    p = info["owner_takes"].mean()
    rows.append(dict(check="same day, same side, zone tests within 2 minutes", pairs=len(pairs),
                     same_decision=np.mean(pairs) if pairs else np.nan, chance=p ** 2 + (1 - p) ** 2))
    # b) the 5 most similar clips (tape at 90 s after the test), other days only
    at = full[full["t_since_test"] == 90].set_index("clip")[lr.FEATS]
    at = at.loc[at.index.isin(info["clip"])]
    z = (at - at.mean()) / at.std().replace(0, 1)
    dec_ = info.set_index("clip").loc[z.index, "owner_takes"].to_numpy()
    days = info.set_index("clip").loc[z.index, "day"].to_numpy()
    zz = z.to_numpy()
    same = []
    for i in range(len(zz)):
        dist = np.sqrt(((zz - zz[i]) ** 2).sum(1))
        dist[days == days[i]] = np.inf
        nn = np.argsort(dist)[:5]
        same.append((dec_[nn] == dec_[i]).mean())
    rows.append(dict(check="5 most similar clips on other days (tape at 90 s)", pairs=len(zz) * 5,
                     same_decision=float(np.mean(same)), chance=p ** 2 + (1 - p) ** 2))
    # c) reasons in the notes
    keys = {"CVD higher low / lower high": r"higher low|lower high|cvd (?:was|stayed|made)? ?(?:a )?(?:much )?higher|cvd .*lower than",
            "absorption": r"absorption|absorb", "retest / second push": r"retest|restest|second push|second test|2nd",
            "other side came in": r"came in|coming in|stepped in|step in|started coming",
            "sellers/buyers in control": r"in control", "no confirmation": r"no (?:real |good )?confirmation|not (?:much|really)",
            "too fast / news": r"fast|erratic|news|crazy|insane", "zone not valid": r"invalid|not valid|isn't valid|not really an accurate"}
    for k, rx in keys.items():
        has = info["note"].str.lower().str.contains(rx, regex=True)
        rows.append(dict(check=f"note mentions: {k}", pairs=int(has.sum()),
                         same_decision=info.loc[has, "owner_takes"].mean() if has.any() else np.nan, chance=p))
    return pd.DataFrame(rows)


def main() -> None:
    log, contrib, full, clips = cross_validate()
    log = results(log)
    log.to_csv(OUT / "learn_explain_log.csv", index=False)
    feats = [c for c in contrib.columns if c in lr.FEATS]
    g = contrib.groupby("owner_takes")[feats].mean().T
    g.columns = ["bot took, owner skipped" if not c else "bot took, owner took" for c in g.columns]
    vals = contrib.groupby("owner_takes")[[f"value_{f}" for f in feats]].median().T
    vals.index = [i[6:] for i in vals.index]
    vals.columns = ["value when owner skipped" if not c else "value when owner took" for c in vals.columns]
    reasons = g.join(vals)
    reasons["push_difference"] = reasons["bot took, owner skipped"] - reasons["bot took, owner took"]
    reasons = reasons.sort_values("push_difference", ascending=False)
    reasons.to_csv(OUT / "learn_explain_reasons.csv")
    cons = consistency(full, clips)
    cons.to_csv(OUT / "owner_consistency.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 60)
    t = log[log["bot_takes"]]
    print("bot entries on held-out clips:", len(t), "| owner skipped:", int((~t["owner_takes"]).sum()),
          "| $ per trade, owner took / skipped:",
          round(lr.usd(t.loc[t["owner_takes"], "bot_points"]).mean(), 1), "/", round(lr.usd(t.loc[~t["owner_takes"], "bot_points"]).mean(), 1))
    print(reasons.round(3).head(12).to_string())
    print(cons.round(3).to_string())


if __name__ == "__main__":
    main()
