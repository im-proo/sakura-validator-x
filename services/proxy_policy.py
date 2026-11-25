"""How many proxies, shards and workers one upload needs.

The policy is deliberately one table, so a run's shape is predictable:

    cookies      proxies (per instance)   shards
    <= 500       1                        1
    <= 1,000     2                        1
    <= 1,500     3                        1
    <= 2,000     4                        1  (no split below the threshold)
    > 2,000      ceil(part/500), max 4    one part per online instance

Above the split threshold the file is cut into one equal part per instance, so
4,500 cookies become four ~1,125-cookie parts with three proxies each.

Workers follow proxies instead of a flat number: one IP carrying 300 concurrent
checks gets that batch flagged, so an instance is asked for
`CHECKER_CONCURRENT_PER_PROXY` workers per proxy it holds.
"""

from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class ProxyPlan:
    """The shape of one check run (whole file, or one of its shards)."""

    cookies: int
    shards: int
    cookies_per_shard: int
    proxies: int
    workers: int

    def describe(self) -> str:
        return (
            f"{self.cookies:,} cookies -> {self.shards} shard(s) of "
            f"{self.cookies_per_shard:,}, {self.proxies} proxy(ies), "
            f"{self.workers} workers"
        )


def proxies_for(cookies: int) -> int:
    """Proxies one instance should hold for `cookies` cookies."""
    per_proxy = max(1, int(config.settings.cookies_per_proxy))
    cap = max(1, int(config.settings.max_proxies_per_api))
    cookies = int(cookies or 0)
    if cookies <= 0:
        return 1
    return max(1, min(cap, -(-cookies // per_proxy)))


def workers_for(proxies: int) -> int:
    """Concurrency that keeps each proxy at a survivable request rate."""
    per_proxy = max(1, int(config.settings.concurrent_per_proxy))
    cap = max(1, int(config.settings.max_workers_per_api))
    return max(1, min(cap, max(1, int(proxies)) * per_proxy))


def shards_for(cookies: int, online: int) -> int:
    """How many instances a file of this size should be spread over."""
    threshold = max(1, int(config.settings.checker_split_threshold))
    if int(cookies or 0) <= threshold:
        return 1
    return max(1, min(int(config.settings.checker_max_shard_apis), max(1, int(online))))


def plan_for(cookies: int, online: int = 1) -> ProxyPlan:
    """Full plan for a file (pass the slice size when it is already split)."""
    cookies = max(0, int(cookies or 0))
    shards = shards_for(cookies, online)
    per_shard = -(-cookies // shards) if cookies else 0
    proxies = proxies_for(per_shard)
    return ProxyPlan(
        cookies=cookies,
        shards=shards,
        cookies_per_shard=per_shard,
        proxies=proxies,
        workers=workers_for(proxies),
    )
