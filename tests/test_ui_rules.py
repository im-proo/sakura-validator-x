"""UI law enforcement: headings, blockquotes, footers, styled buttons."""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone
from typing import Any

from ui import keyboards as K
from ui import renderer as R
from ui.typography import BRAND_FOOTER

ROOT = pathlib.Path(__file__).resolve().parents[1]

FORBIDDEN_EMOJI = ("✅", "❌", "🔥", "💚", "💙", "🟢", "🔴", "✨")

USER = {
    "user_id": 123456789,
    "username": "sakura",
    "full_name": "Sakura Chan",
    "plan": "premium",
    "plan_expires": datetime(2027, 1, 1, tzinfo=timezone.utc),
    "daily_attempts_used": 3,
    "total_checks": 42,
    "total_hits": 128,
    "total_premium_hits": 31,
    "joined_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
    "is_banned": False,
}

HIT = {
    "account": {
        "name": "Lisaan",
        "email": "user@example.com",
        "country": "IN",
        "country_flag": "🇮🇳",
        "plan": "Premium",
        "member_since": "June 2026",
        "next_billing": "2026-11-20",
        "payment": "UPI",
        "card": "None",
        "phone": "+91 90000 00000",
        "video_quality": "UHD",
        "streams": 4,
        "price": "₹649",
        "hold_status": "No",
        "extra_member": "No",
        "is_on_hold": False,
        "email_verified": True,
        "membership_status": "CURRENT_MEMBER",
        "profiles": ["a", "b"],
        "user_guid": "ABC123",
        "valid_till_utc": "2026-11-14 04:53:16 UTC",
        "remaining": "0h 58m 59s",
    },
    "cookie": "cookie-value",
    "nf_token": {"phone_link": "https://p", "pc_link": "https://c", "tv_link": "https://t"},
}


class FakeSession:
    """Just enough of CheckSession for the renderer."""

    from models.schemas import SessionStats as _Stats

    stats = _Stats(total=1000, checked=450, hits=8, premium=6, free=2, bad=380, errors=18, hold=2, cpm=14230.0)


def screens() -> dict[str, Any]:
    return {
        "main_menu": R.render_main_menu_card(USER, 4),
        "verify": R.render_verify_card(
            [{"channel_name": "Updates", "channel_link": "https://t.me/x"}]
        ),
        "instructions": R.render_check_instructions_card(USER),
        "confirm": R.render_cookie_confirm_card(USER, {"file_name": "c.txt", "estimated_count": 500}),
        "live": R.render_live_card(FakeSession()),
        "hit": R.render_hit_card(HIT),
        "complete": R.render_complete_card(FakeSession()),
        "stats": R.render_stats_card(USER, 4),
        "leaderboard": R.render_leaderboard_card(
            [{"user_id": 1, "username": "a", "total_hits": 5}], USER["user_id"], 9
        ),
        "help": R.render_help_card(),
        "updates": R.render_updates_card(),
        "plans": R.render_plans_card(USER),
        "receipt": R.render_receipt_card(
            {"receipt_id": "RCP-1", "plan": "sakura", "days": 0, "expires": None}, USER
        ),
        "expired": R.render_plan_expired_card("basic"),
        "limit": R.render_daily_limit_card(USER),
        "error": R.render_error_card("check failed", "checker stopped responding"),
        "banned": R.render_banned_card("spam"),
        "maintenance": R.render_maintenance_card(),
        "loading": R.render_loading_card(),
        "admin_home": R.render_admin_dashboard_card(
            {"total_users": 10},
            {"online": 2, "total": 2, "all_online": True, "avg_cpm": 9000},
            {"cpu": 12, "ram": 40, "disk": 55, "uptime": "3d"},
            1,
        ),
        "admin_users": R.render_admin_users_card({"rows": [USER], "page": 0, "pages": 1}),
        "admin_user": R.render_admin_user_card(USER, 4),
        "admin_apis": R.render_admin_apis_card(
            [{"api_id": "api-01", "name": "Oracle", "status": "online", "active_jobs": 1}],
            {"online": 1, "total": 1, "avg_cpm": 1},
        ),
        "admin_api": R.render_admin_api_card({"api_id": "api-01", "url": "https://x", "status": "online"}),
        "admin_channels": R.render_admin_channels_card(
            [{"channel_name": "x", "channel_id": "@x", "is_active": True}]
        ),
        "admin_videos": R.render_admin_videos_card({"main_menu_video": "AgAC"}),
        "admin_keys": R.render_admin_keys_card({"basic": {"unused": 2, "used": 1}}, ["SAKURA-AAAA-BBBB-CCCC"]),
        "admin_broadcast": R.render_admin_broadcast_card(),
        "admin_broadcast_preview": R.render_admin_broadcast_preview_card("hello", 10),
        "admin_config": R.render_admin_config_card(
            {"maintenance_mode": False, "api_rotation_mode": "priority", "max_concurrent": 20}, {}
        ),
        "admin_logs": R.render_admin_logs_card(
            [{"action": "ban", "target": "1", "performed_at": datetime.now(timezone.utc)}], 0, 1
        ),
        "admin_stats": R.render_admin_bot_stats_card(
            {"total_users": 1, "active_sessions": 0, "cpu_percent": 3, "ram_percent": 4, "api_health": {}}
        ),
    }


def block_kind(block: Any) -> str:
    return getattr(block.type, "value", block.type)


def collect_text(block: Any) -> list[str]:
    """Every string inside a block tree (rich text, tables, quotations)."""
    found: list[str] = []
    text = getattr(block, "text", None)
    if isinstance(text, str):
        found.append(text)
    elif text is not None and hasattr(text, "text"):
        found.append(str(text.text))
    for sub in getattr(block, "blocks", []) or []:
        found.extend(collect_text(sub))
    for row in getattr(block, "cells", []) or []:
        for cell in row:
            if getattr(cell, "text", None) is not None:
                found.extend(collect_text(cell))
    return found


def test_every_screen_opens_with_a_heading():
    for name, card in screens().items():
        assert block_kind(card.blocks[0]) == "heading", f"{name} has no heading"
        assert collect_text(card.blocks[0])[0].strip(), f"{name} has an empty heading"


def test_every_screen_ends_with_the_brand_line():
    for name, card in screens().items():
        last_text = " ".join(collect_text(card.blocks[-1]))
        assert BRAND_FOOTER in last_text, f"{name} is missing the brand footer"


def test_every_screen_uses_at_least_one_blockquote():
    for name, card in screens().items():
        kinds = [block_kind(b) for b in card.blocks]
        assert "blockquote" in kinds, f"{name} has no blockquoted content"


def test_blockquotes_are_never_nested():
    for name, card in screens().items():
        for block in card.blocks:
            if block_kind(block) != "blockquote":
                continue
            inner = [block_kind(sub) for sub in (block.blocks or [])]
            assert "blockquote" not in inner, f"{name} nests a blockquote"
            assert "expandable_blockquote" not in inner, f"{name} nests an expandable"


def test_tables_are_bordered_and_compact():
    def walk(blocks, name):
        for block in blocks:
            kind = block_kind(block)
            if kind == "table":
                assert block.is_bordered is True, f"{name} table not bordered"
                assert block.is_compact is True, f"{name} table not compact"
            for sub in getattr(block, "blocks", []) or []:
                walk([sub], name)

    for name, card in screens().items():
        walk(card.blocks, name)


def test_brand_footer_count_matches_minimum():
    source = (ROOT / "ui" / "renderer.py").read_text(encoding="utf-8")
    assert source.count("BRAND_FOOTER") >= 15


def test_hit_card_keeps_the_single_allowed_flag_emoji():
    card = screens()["hit"]
    text = " ".join(collect_text(card.blocks[2]))
    assert "🇮🇳" in text


def test_no_forbidden_emoji_outside_the_download_artifact():
    """Shipped code may not use colour emoji except the hit .txt marker."""
    scanned = [
        path
        for folder in ("ui", "handlers", "services", "core", "models")
        for path in (ROOT / folder).rglob("*.py")
    ] + [ROOT / "bot.py", ROOT / "config.py"]

    offenders: dict[str, list[str]] = {}
    for path in scanned:
        content = path.read_text(encoding="utf-8")
        hits = [ch for ch in FORBIDDEN_EMOJI if ch in content]
        if hits:
            offenders[str(path.relative_to(ROOT))] = hits

    # Only the buyer-facing .txt builder in file_service may use one marker.
    assert set(offenders) <= {"services/file_service.py"}, offenders
    assert set(offenders.get("services/file_service.py", [])) <= {"✅"}


# ── keyboards ────────────────────────────────────────────────────────
def keyboards_under_test() -> list[tuple[str, Any, int]]:
    """(name, markup, max buttons allowed) from the spec."""
    return [
        ("main_menu", K.main_menu_keyboard(), 6),
        ("instructions", K.check_instructions_keyboard(), 6),
        ("confirm", K.cookie_confirm_keyboard(), 6),
        ("live", K.live_check_keyboard("job-1"), 6),
        ("hit_login", K.hit_login_keyboard(HIT["nf_token"]), 3),
        ("complete", K.check_complete_keyboard(), 6),
        ("back", K.back_to_menu_keyboard(), 6),
        ("limit", K.daily_limit_keyboard(), 6),
        ("plans", K.plans_keyboard(), 6),
        ("expired", K.plan_expired_keyboard(), 6),
        ("verify", K.verify_keyboard(
            [{"channel_name": "A", "channel_link": "https://a"},
             {"channel_name": "B", "channel_link": "https://b"}]
        ), 6),
        ("admin_home", K.admin_dashboard_keyboard(), 9),
        ("admin_users", K.admin_users_keyboard(0, 3), 6),
        ("admin_user", K.admin_user_actions_keyboard(1, False), 6),
        ("admin_plan", K.admin_user_plan_keyboard(1), 6),
        ("admin_apis", K.admin_apis_keyboard([{"api_id": "api-01", "name": "Oracle"}]), 6),
        ("admin_api", K.admin_api_actions_keyboard("api-01", True), 6),
        ("admin_channels", K.admin_channels_keyboard(
            [{"channel_name": "x", "channel_id": "@x", "is_active": True}]
        ), 6),
        ("admin_videos", K.admin_videos_keyboard({}), 6),
        ("admin_keys", K.admin_keys_keyboard(), 6),
        ("admin_duration", K.admin_key_duration_keyboard("basic"), 6),
        ("admin_broadcast", K.admin_broadcast_confirm_keyboard(), 6),
        ("admin_config", K.admin_config_keyboard({}), 6),
        ("admin_logs", K.admin_logs_keyboard(0, 2), 6),
        ("admin_stats", K.admin_bot_stats_keyboard(), 6),
        ("cancel", K.cancel_keyboard("x"), 6),
        ("confirm_delete", K.confirm_delete_keyboard("a", "b"), 6),
    ]


def test_every_button_is_styled():
    for name, markup, _limit in keyboards_under_test():
        for row in markup.inline_keyboard:
            for btn in row:
                assert btn.style in ("primary", "success", "danger", "link"), (
                    f"{name}: button {btn.text!r} has no valid style"
                )


def test_button_budget_per_screen_and_row():
    for name, markup, limit in keyboards_under_test():
        total = sum(len(row) for row in markup.inline_keyboard)
        assert total <= limit, f"{name} has {total} buttons (max {limit})"
        for row in markup.inline_keyboard:
            allowed = 3 if name == "hit_login" else 2
            assert len(row) <= allowed, f"{name} row has {len(row)} buttons"


def test_callback_payloads_fit_telegram_limit():
    for name, markup, _limit in keyboards_under_test():
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    assert len(btn.callback_data.encode("utf-8")) <= 64, (
                        f"{name}: callback too long: {btn.callback_data}"
                    )


def test_buttons_have_symbol_prefix_or_are_short_labels():
    allowed = ("◆", "◇", "✦", "▷", "▤", "♕", "ⓘ", "↻", "♡")
    for name, markup, _limit in keyboards_under_test():
        for row in markup.inline_keyboard:
            for btn in row:
                assert btn.text.startswith(allowed) or btn.text in ("◇ PC", "✦ TV"), (
                    f"{name}: button {btn.text!r} lacks an approved symbol"
                )
