"""Typography and symbol rules (Section 9.2 / 9.3)."""

from __future__ import annotations

from datetime import datetime, timezone

from ui.typography import (
    BOT_NAME_DISPLAY,
    BRAND_FOOTER,
    SYMBOLS,
    country_flag,
    esc,
    fmt_dt,
    fmt_num,
    minutes_label,
    plan_badge,
    progress_bar,
    sc,
    truncate,
)


def test_small_caps_translates_letters_only():
    assert sc("Daily Limit") == "ᴅᴀɪʟʏ ʟɪᴍɪᴛ"
    assert sc("/start 100%") == "/sᴛᴀʀᴛ 100%"
    assert sc("") == ""


def test_brand_constants_are_exact():
    assert BOT_NAME_DISPLAY == "𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗"
    assert BRAND_FOOTER.startswith("◈ ")
    assert "Bᴏᴛs" in BRAND_FOOTER
    assert BRAND_FOOTER.endswith("♡")


def test_symbol_set_is_locked_and_colourless():
    assert set(SYMBOLS) == {"◆", "◇", "‣", "◈", "→", "✦", "▷", "▤", "♕", "ⓘ", "↻", "♡"}
    for symbol in SYMBOLS:
        assert not any(ord(ch) > 0x1F000 for ch in symbol), f"colour emoji in symbol set: {symbol}"


def test_fmt_num_groups_thousands():
    assert fmt_num(1000) == "1,000"
    assert fmt_num(1_000_000) == "1,000,000"
    assert fmt_num(None) == "0"
    assert fmt_num(14230.6) == "14,231"


def test_progress_bar_clamps_and_reports_percent():
    assert progress_bar(0, 100) == "[" + "░" * 20 + "] 0%"
    assert progress_bar(100, 100) == "[" + "█" * 20 + "] 100%"
    assert progress_bar(50, 100) == "[" + "█" * 10 + "░" * 10 + "] 50%"
    assert progress_bar(500, 0).endswith("0%")
    assert progress_bar(900, 100).endswith("100%")


def test_plan_badges():
    assert plan_badge("sakura") == "♕ 𝐒𝐚𝐤𝐮𝐫𝐀"
    assert plan_badge("premium").startswith("◆")
    assert plan_badge("basic").startswith("◇")
    assert plan_badge("mystery").startswith("◇")


def test_country_flag_uses_api_value_first():
    assert country_flag("IN") == "🇮🇳"
    assert country_flag("IN", "🇺🇸") == "🇺🇸"
    assert country_flag("ZZ") == "🇿🇿"
    assert country_flag(None) == ""


def test_helpers_never_render_none():
    assert fmt_dt(None) == sc("lifetime")
    assert fmt_dt(datetime(2026, 11, 15, tzinfo=timezone.utc)) == "2026-11-15"
    assert truncate("x" * 50, 10) == "x" * 9 + "…"
    assert minutes_label(59) == "0m"
    assert minutes_label(3600) == "1h 00m"
    assert esc("<b>&") == "&lt;b&gt;&amp;"
