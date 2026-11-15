"""MongoDB-backed integration tests for user/plan/key flows.

Skipped automatically when no MongoDB is reachable, so the suite still
passes on a machine without a database. Runs against a throwaway
database (`sakura_test_pytest`) that is dropped afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any, AsyncIterator

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

import config
import core.database as database
from models.schemas import utcnow
from services import key_service, plan_service

TEST_DB = "sakura_test_pytest"


class FakeTgUser:
    """Minimal aiogram User stand-in."""

    def __init__(self, user_id: int = 999001, username: str = "tester") -> None:
        self.id = user_id
        self.username = username
        self.full_name = "Sakura Tester"


@contextlib.asynccontextmanager
async def temp_db() -> AsyncIterator[Any]:
    # Mirror production settings (tz_aware) so round-trips match the bot.
    client = AsyncIOMotorClient(
        config.settings.mongo_uri, serverSelectionTimeoutMS=1500, tz_aware=True
    )
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        pytest.skip("no local MongoDB available")

    test_db = client[TEST_DB]
    database._client = client
    database._db = test_db
    await test_db.users.delete_many({})
    await test_db.plan_keys.delete_many({})
    await test_db.users.create_index("user_id", unique=True)
    await test_db.plan_keys.create_index("key", unique=True)
    try:
        yield test_db
    finally:
        await client.drop_database(TEST_DB)
        database._client = None
        database._db = None
        client.close()


def test_ensure_user_creates_then_updates_without_conflict():
    """Regression: $set and $setOnInsert must not touch the same path."""

    async def body() -> None:
        async with temp_db() as db:
            first = await plan_service.ensure_user(FakeTgUser())
            assert first["plan"] == "free"
            assert first["daily_attempts_used"] == 0
            assert first["total_hits"] == 0

            second = await plan_service.ensure_user(FakeTgUser(username="renamed"))
            assert second["user_id"] == 999001
            assert second["username"] == "renamed"
            assert second["joined_at"] == first["joined_at"], "joined_at must be insert-only"
            assert await db.users.count_documents({"user_id": 999001}) == 1

    asyncio.run(body())


def test_free_plan_attempt_cap_is_enforced_atomically():
    async def body() -> None:
        async with temp_db() as db:
            await plan_service.ensure_user(FakeTgUser())
            results = [await plan_service.deduct_attempt(999001, "free") for _ in range(7)]
            assert results == [True] * 5 + [False] * 2
            doc = await db.users.find_one({"user_id": 999001})
            assert doc["daily_attempts_used"] == 5

    asyncio.run(body())


def test_sakura_plan_is_unlimited():
    async def body() -> None:
        async with temp_db() as db:
            await plan_service.ensure_user(FakeTgUser())
            await plan_service.grant_plan(999001, "sakura", None)
            for _ in range(6):
                assert await plan_service.deduct_attempt(999001, "sakura") is True
            doc = await db.users.find_one({"user_id": 999001})
            assert doc["plan"] == "sakura"
            assert doc["plan_expires"] is None

    asyncio.run(body())


def test_grant_paid_plan_sets_and_extends_expiry():
    async def body() -> None:
        async with temp_db():
            await plan_service.ensure_user(FakeTgUser())
            first = await plan_service.grant_plan(999001, "premium", 30)
            assert first is not None
            delta = first["plan_expires"] - utcnow()
            assert timedelta(days=29, hours=23) < delta <= timedelta(days=30, minutes=1)

            extended = await plan_service.grant_plan(999001, "premium", 30)
            gained = extended["plan_expires"] - first["plan_expires"]
            assert timedelta(days=29, hours=23) < gained <= timedelta(days=30, minutes=1)

    asyncio.run(body())


def test_expired_plan_falls_back_to_free():
    async def body() -> None:
        async with temp_db() as db:
            await plan_service.ensure_user(FakeTgUser())
            await db.users.update_one(
                {"user_id": 999001},
                {"$set": {"plan": "basic", "plan_expires": utcnow() - timedelta(hours=1)}},
            )
            doc = await db.users.find_one({"user_id": 999001})
            refreshed, expired = await plan_service.expire_plan_if_needed(doc)
            assert expired == "basic"
            assert refreshed["plan"] == "free"
            assert refreshed["plan_expires"] is None

    asyncio.run(body())


def test_daily_reset_is_lazy_and_idempotent():
    async def body() -> None:
        async with temp_db() as db:
            await plan_service.ensure_user(FakeTgUser())
            await db.users.update_one(
                {"user_id": 999001},
                {"$set": {"daily_attempts_used": 4, "daily_reset_at": utcnow() - timedelta(minutes=1)}},
            )
            doc = await db.users.find_one({"user_id": 999001})
            refreshed = await plan_service.refresh_daily(doc)
            assert refreshed["daily_attempts_used"] == 0
            assert refreshed["daily_reset_at"] > utcnow()

            again = await plan_service.refresh_daily(refreshed)
            assert again["daily_attempts_used"] == 0

    asyncio.run(body())


def test_key_generation_and_redemption_round_trip():
    async def body() -> None:
        async with temp_db() as db:
            await plan_service.ensure_user(FakeTgUser())
            keys = await key_service.generate_keys("premium", 30, 2, created_by=1)
            assert len(keys) == 2
            assert len(set(keys)) == 2

            assert await db.plan_keys.count_documents({"is_used": False}) == 2

            result = await key_service.redeem_key(keys[0].lower(), 999001)
            assert result["ok"] is True
            assert result["plan"] == "premium"
            assert result["receipt_id"].startswith("RCP-")

            user = await plan_service.get_user(999001)
            assert user["plan"] == "premium"
            assert user["plan_expires"] is not None

            # second use of the same key must fail
            again = await key_service.redeem_key(keys[0], 999001)
            assert again["ok"] is False
            assert "already" in again["error"]

            bogus = await key_service.redeem_key("SAKURA-ZZZZ-ZZZZ-ZZZZ", 999001)
            assert bogus["ok"] is False

    asyncio.run(body())


def test_leaderboard_rank_and_points():
    async def body() -> None:
        async with temp_db() as db:
            await plan_service.ensure_user(FakeTgUser(1))
            await plan_service.ensure_user(FakeTgUser(2))
            await plan_service.record_check_result(1, checks=10, hits=4, premium_hits=2)
            await plan_service.record_check_result(2, checks=10, hits=1, premium_hits=0)

            assert await plan_service.get_rank(1) == 1
            assert await plan_service.get_rank(2) == 2
            top = await plan_service.top_users(10)
            assert [row["user_id"] for row in top] == [1, 2]

            doc = await db.users.find_one({"user_id": 1})
            assert doc["total_hits"] == 4
            assert doc["total_premium_hits"] == 2
            assert doc["leaderboard_points"] == 4 * 10 + 2 * 25

    asyncio.run(body())
