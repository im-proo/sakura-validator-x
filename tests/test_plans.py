"""Plan matrix, attempt accounting and session maths (Section 6 / 14)."""

from __future__ import annotations

from datetime import timedelta

from models.schemas import (
    PLAN_LIMITS,
    SessionStats,
    concurrency,
    daily_limit,
    hit_identity,
    is_premium_hit,
    max_cookies,
    next_midnight_utc,
    utcnow,
)
from services.plan_service import attempts_left, limit_reached


def test_plan_matrix_matches_the_spec():
    assert daily_limit("free") == 5
    assert daily_limit("basic") == 10
    assert daily_limit("premium") == 20
    assert daily_limit("sakura") is None

    assert max_cookies("free") == 1_000
    assert max_cookies("basic") == 10_000
    assert max_cookies("premium") == 100_000
    assert max_cookies("sakura") == 500_000

    assert concurrency("free") == 1
    assert concurrency("sakura") == 5
    assert set(PLAN_LIMITS) == {"free", "basic", "premium", "sakura"}


def test_unknown_plan_falls_back_to_free():
    assert daily_limit("platinum") == 5
    assert max_cookies("platinum") == 1_000


def test_attempts_left_is_unlimited_for_sakura():
    sakura = {"plan": "sakura", "daily_attempts_used": 9999}
    assert attempts_left(sakura) is None
    assert limit_reached(sakura) is False


def test_attempts_left_counts_down_and_clamps():
    assert attempts_left({"plan": "premium", "daily_attempts_used": 0}) == 20
    assert attempts_left({"plan": "premium", "daily_attempts_used": 19}) == 1
    assert attempts_left({"plan": "premium", "daily_attempts_used": 20}) == 0
    assert attempts_left({"plan": "premium", "daily_attempts_used": 25}) == 0
    assert limit_reached({"plan": "free", "daily_attempts_used": 5}) is True
    assert limit_reached({"plan": "free", "daily_attempts_used": 4}) is False


def test_hit_identity_deduplicates_accounts():
    first = {"account": {"email": "User@Example.com"}, "cookie": "a"}
    second = {"account": {"email": "user@example.com"}, "cookie": "b"}
    assert hit_identity(first) == hit_identity(second)

    guid_only = {"account": {"user_guid": "ABC"}, "cookie": "c"}
    assert hit_identity(guid_only) == "guid:ABC"

    cookie_only = {"account": {}, "cookie": "raw"}
    assert hit_identity(cookie_only).startswith("cookie:")


def test_premium_classification():
    assert is_premium_hit({"account": {"plan": "Premium"}}) is True
    assert is_premium_hit({"account": {"plan": "premium"}}) is True
    assert is_premium_hit({"account": {"plan": "Standard"}}) is False
    assert is_premium_hit({"account": {"plan": "Basic"}}) is False
    assert is_premium_hit({"account": {"is_premium": True}}) is True


def test_session_stats_progress_and_labels():
    stats = SessionStats(total=1000, checked=450)
    assert stats.percent == 45

    empty = SessionStats(total=0, checked=0)
    assert empty.percent == 0

    fresh = SessionStats()
    fresh.started_at = utcnow() - timedelta(seconds=65)
    assert fresh.elapsed_label == "1m 05s"

    fresh.started_at = utcnow() - timedelta(seconds=8)
    assert fresh.elapsed_label.endswith("s")
    assert 7 <= fresh.elapsed_seconds <= 10

    long_run = SessionStats()
    long_run.started_at = utcnow() - timedelta(hours=2, minutes=11)
    assert long_run.elapsed_label.startswith("2h")


def test_next_midnight_is_utc_midnight_ahead():
    moment = utcnow()
    midnight = next_midnight_utc(moment)
    assert midnight.hour == 0 and midnight.minute == 0 and midnight.second == 0
    assert midnight > moment
    assert midnight.tzinfo is not None
