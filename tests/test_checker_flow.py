"""End-to-end run of the check pipeline with a fake API and fake bot.

This exercises the riskiest path offline: submit → poll → deliver hits →
finalize → release, including the attempt-spending rule.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, patch

import services.checker_service as cs
from models.schemas import SessionStats

HIT = {
    "status": "hit",
    "account": {
        "name": "Lisaan",
        "email": "user@example.com",
        "country": "IN",
        "country_flag": "🇮🇳",
        "plan": "Premium",
        "email_verified": True,
        "profiles": ["a"],
        "user_guid": "ABC",
        "valid_till_utc": "2026-11-14 04:53:16 UTC",
        "remaining": "0h 58m 59s",
    },
    "nf_token": {"phone_link": "https://p", "pc_link": "https://c", "tv_link": "https://t"},
    "cookie": "NetflixId=abc",
}


class FakeMessage:
    message_id = 99


class FakeBot:
    def __init__(self) -> None:
        self.edits: list[dict[str, Any]] = []
        self.rich: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.copies: list[dict[str, Any]] = []

    async def edit_message_text(self, **kwargs: Any) -> bool:
        self.edits.append(kwargs)
        return True

    async def send_rich_message(self, **kwargs: Any) -> FakeMessage:
        self.rich.append(kwargs)
        return FakeMessage()

    async def send_document(self, **kwargs: Any) -> FakeMessage:
        self.documents.append(kwargs)
        return FakeMessage()

    async def copy_message(self, **kwargs: Any) -> FakeMessage:
        self.copies.append(kwargs)
        return FakeMessage()


def make_session(tmp_path, hit_mode: str = "premium_only") -> cs.CheckSession:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tv\n", encoding="utf-8")
    session = cs.CheckSession(
        session_id=cs.new_session_id(),
        user_id=555,
        chat_id=777,
        message_id=42,
        api_id="api-01",
        api_url="https://nf-01.example.com",
        hit_mode=hit_mode,
        plan="premium",
        stats=SessionStats(status="running"),
    )
    session.__dict__["_file"] = str(cookie_file)
    return session


def run(session, bot, status_side_effect, hits=None, cancel=False):
    """Drive run_check with patched collaborators."""
    hits_payload = {"hits": hits or [], "total_pages": 1, "page": 1, "total_hits": len(hits or [])}
    with patch.object(cs, "POLL_INTERVAL", 0), patch.object(cs, "MIN_EDIT_INTERVAL", 0), patch.object(
        cs.checker_api, "submit_job_file", AsyncMock(return_value={"job_id": "job-1", "total": 3})
    ) as submit, patch.object(
        cs.checker_api, "get_job_status", AsyncMock(side_effect=status_side_effect)
    ) as status, patch.object(
        cs.checker_api, "get_job_hits", AsyncMock(return_value=hits_payload)
    ) as hits_mock, patch.object(
        cs.checker_api, "cancel_job", AsyncMock(return_value={"status": "cancelled"})
    ) as cancel_mock, patch.object(
        cs.plan_service, "deduct_attempt", AsyncMock(return_value=True)
    ) as deduct, patch.object(
        cs.plan_service, "record_check_result", AsyncMock()
    ) as record, patch.object(
        cs.database, "get_config", AsyncMock(return_value="")
    ), patch.object(
        cs.api_manager, "drop_session"
    ), patch.object(
        cs.api_manager, "is_cancelled", return_value=cancel
    ), patch.object(
        cs.api_manager.balancer, "release", AsyncMock()
    ) as release:
        asyncio.run(cs.run_check(bot, session, session.__dict__["_file"], "cookies.txt"))

    return {
        "submit": submit,
        "status": status,
        "hits": hits_mock,
        "cancel": cancel_mock,
        "deduct": deduct,
        "record": record,
        "release": release,
    }


def test_successful_run_delivers_hits_files_and_summary(tmp_path):
    bot = FakeBot()
    session = make_session(tmp_path)
    calls = run(
        session,
        bot,
        status_side_effect=[
            {"status": "running", "checked": 2, "total": 3, "hits": 1, "bad": 1, "cpm": 900.0},
            {"status": "done", "checked": 3, "total": 3, "hits": 1, "bad": 2, "cpm": 1000.0},
        ],
        hits=[HIT],
    )

    calls["submit"].assert_awaited_once()
    calls["deduct"].assert_awaited_once()
    calls["release"].assert_awaited_once()

    # hit card + completion card
    assert len(bot.rich) == 2, [r.get("rich_message") for r in bot.rich]
    # the hit cookie file now rides inside the hit card, so only the premium
    # zip and premium txt are separate documents
    assert len(bot.documents) == 2

    def _has_document(block):
        if type(block).__name__ == "InputRichBlockDocument":
            return True
        return any(_has_document(inner) for inner in (getattr(block, "blocks", None) or []))

    hit_card = bot.rich[0]["rich_message"]
    assert any(_has_document(block) for block in hit_card.blocks), "cookie file must be embedded"

    assert session.stats.hits == 1
    assert session.stats.premium == 1
    assert session.stats.checked == 3
    assert session.stats.status == "done"

    # aiogram carries the name on the InputFile, not as a kwarg
    files = [getattr(d.get("document"), "filename", "") for d in bot.documents]
    assert any(name.endswith(".zip") for name in files)
    assert any(name.endswith(".txt") for name in files)
    assert not os.path.exists(str(session.__dict__["_file"])), "upload must be cleaned up"

    record_args = calls["record"].await_args
    assert record_args.kwargs["hits"] == 1
    assert record_args.kwargs["premium_hits"] == 1


def test_hits_are_delivered_only_once(tmp_path):
    bot = FakeBot()
    session = make_session(tmp_path)
    run(
        session,
        bot,
        status_side_effect=[
            {"status": "running", "checked": 1, "total": 3, "hits": 1, "cpm": 10.0},
            {"status": "running", "checked": 2, "total": 3, "hits": 1, "cpm": 10.0},
            {"status": "done", "checked": 3, "total": 3, "hits": 1, "cpm": 10.0},
        ],
        hits=[HIT],
    )
    # one hit card regardless of three fetches
    assert len(bot.rich) == 2


def test_cancelled_run_still_releases_and_reports(tmp_path):
    bot = FakeBot()
    session = make_session(tmp_path, hit_mode="all")
    calls = run(
        session,
        bot,
        status_side_effect=[{"status": "running", "checked": 1, "total": 3, "hits": 0}],
        cancel=True,
    )

    calls["cancel"].assert_awaited()
    calls["release"].assert_awaited_once()
    assert session.stats.status == "cancelled"
    assert len(bot.rich) == 1  # completion card only, no hits


def test_zero_hit_completion_is_warm_not_cold(tmp_path):
    bot = FakeBot()
    session = make_session(tmp_path)
    run(
        session,
        bot,
        status_side_effect=[{"status": "done", "checked": 3, "total": 3, "hits": 0, "bad": 3}],
        hits=[],
    )

    complete = bot.rich[-1]["rich_message"]
    text = " ".join(
        getattr(block, "text", "") if isinstance(getattr(block, "text", ""), str) else ""
        for block in complete.blocks
    )
    joined = " ".join(
        str(getattr(sub, "text", ""))
        for block in complete.blocks
        for sub in (getattr(block, "blocks", None) or [])
    )
    from ui.typography import sc

    assert sc("no hits this run") in (text + joined)
    assert not any(d["filename"].endswith((".zip", ".txt")) for d in bot.documents)


def test_status_fields_map_from_go_snapshot():
    """Go exposes non-hits as `expired` and speed as `current_rpm`."""
    from types import SimpleNamespace

    from models.schemas import SessionStats

    session = SimpleNamespace(stats=SessionStats())
    cs._apply_status(
        session,
        {
            "id": "job-1",
            "status": "running",
            "total": 10,
            "checked": 4,
            "hits": 1,
            "expired": 3,
            "errors": 0,
            "duplicates": 0,
            "current_rpm": 812.5,
        },
    )
    assert session.stats.checked == 4
    assert session.stats.hits == 1
    assert session.stats.bad == 3
    assert session.stats.cpm == 812.5


def test_free_row_is_hits_minus_premium():
    """valid = premium + free must always hold on the dashboard."""
    from types import SimpleNamespace

    from models.schemas import SessionStats

    session = SimpleNamespace(stats=SessionStats())
    session.stats.premium = 2
    cs._apply_status(session, {"status": "running", "total": 10, "checked": 9, "hits": 5, "expired": 4})
    assert session.stats.hits == 5
    assert session.stats.premium == 2
    assert session.stats.free == 3
