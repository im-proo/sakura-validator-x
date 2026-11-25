"""Sizing rules: cookies -> proxies, shards and workers."""

from __future__ import annotations

import config
from services import proxy_policy


def test_proxy_count_follows_the_table():
    assert proxy_policy.proxies_for(0) == 1
    assert proxy_policy.proxies_for(1) == 1
    assert proxy_policy.proxies_for(500) == 1
    assert proxy_policy.proxies_for(501) == 2
    assert proxy_policy.proxies_for(1000) == 2
    assert proxy_policy.proxies_for(1001) == 3
    assert proxy_policy.proxies_for(1500) == 3
    assert proxy_policy.proxies_for(1501) == 4
    assert proxy_policy.proxies_for(4500) == 4


def test_no_split_at_or_below_threshold():
    plan = proxy_policy.plan_for(2000, online=4)
    assert plan.shards == 1
    assert plan.cookies_per_shard == 2000
    assert plan.proxies == 4


def test_split_into_one_part_per_instance_above_threshold():
    plan = proxy_policy.plan_for(4500, online=4)
    assert plan.shards == 4
    assert plan.cookies_per_shard == 1125
    assert plan.proxies == 3
    assert plan.workers == 3 * config.settings.concurrent_per_proxy


def test_split_limited_by_online_instances():
    assert proxy_policy.plan_for(4500, online=2).shards == 2
    assert proxy_policy.plan_for(4500, online=9).shards == config.settings.checker_max_shard_apis


def test_workers_are_bounded():
    assert proxy_policy.workers_for(1) == config.settings.concurrent_per_proxy
    assert proxy_policy.workers_for(4) == 4 * config.settings.concurrent_per_proxy
    assert proxy_policy.workers_for(999) == config.settings.max_workers_per_api


def test_plan_is_describable():
    text = proxy_policy.plan_for(1200).describe()
    assert "1,200 cookies" in text and "3 proxy" in text
