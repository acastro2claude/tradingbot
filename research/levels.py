"""Group daily POCs into levels (STRATEGY.md section 1a)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class PocGroup:
    price: int            # level price in ticks (average of the POCs, rounded to a tick)
    count: int            # number of POCs in the group
    low: int
    high: int
    members: list = field(default_factory=list)


def cluster_pocs(poc_ticks, width_ticks: int) -> list[PocGroup]:
    """Group POCs so that each group's highest and lowest POC are at most `width_ticks` apart.

    Densest first: repeatedly take the window of `width_ticks` that holds the
    most POCs, make it a group, and remove those POCs. POCs left alone come
    back as groups of 1. Ties go to the narrower window, then the lower price.
    """
    remaining = sorted(int(p) for p in poc_ticks)
    groups: list[PocGroup] = []
    while remaining:
        arr = np.asarray(remaining)
        ends = np.searchsorted(arr, arr + width_ticks, side="right")
        counts = ends - np.arange(len(arr))
        best = counts.max()
        cands = np.flatnonzero(counts == best)
        spans = arr[ends[cands] - 1] - arr[cands]
        i = cands[np.lexsort((arr[cands], spans))[0]]
        members = remaining[i:ends[i]]
        groups.append(PocGroup(price=int(np.rint(np.mean(members))), count=len(members),
                               low=members[0], high=members[-1], members=list(members)))
        del remaining[i:ends[i]]
    return sorted(groups, key=lambda g: g.price)
