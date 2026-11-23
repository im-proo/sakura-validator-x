"""Cross-locale plan, price and currency handling.

Netflix localises plan names and bills in the market's own currency, so the
same tier arrives in many words. A Premium account must never be reported as
free, and the hit card must show a real value instead of "Unknown".
"""

from __future__ import annotations

from models.schemas import (
    is_ads_plan,
    is_premium_hit,
    known_text,
    plan_display,
    plan_family,
    price_label,
)
from services.checker_service import CheckSession, _split_counts


def _session() -> CheckSession:
    return CheckSession(
        session_id="job-test",
        user_id=1,
        chat_id=1,
        message_id=1,
        api_id="api-01",
        api_url="http://127.0.0.1:8080",
        hit_mode="premium_only",
        plan="free",
    )


def test_more_locales_are_classified():
    assert plan_family("Standar") == "standard"
    assert plan_family("Temel") == "basic"
    assert plan_family("Cơ bản") == "basic"
    assert plan_family("סטנדרטי") == "standard"
    assert plan_family("Premium") == "premium"
    assert plan_family("פרימיום") == "premium"

    assert is_ads_plan("Standard avec publicité") is True
    assert is_ads_plan("Standar dengan iklan") is True
    assert is_ads_plan("Standard mit Werbung") is True
    assert is_ads_plan("Premium") is False


def test_premium_is_recognised_from_the_plan_code():
    """A localised name the token table misses still has a machine code."""
    hit = {"account": {"plan": "Paket Süper", "plan_code": "PREMIUM"}}
    assert is_premium_hit(hit) is True

    ads = {"account": {"plan": "Paket Süper", "plan_code": "STANDARD_WITH_ADS"}}
    assert is_premium_hit(ads) is False


def test_unknown_values_are_not_printed():
    assert known_text("Unknown") == "—"
    assert known_text(" unknown ") == "—"
    assert known_text(None) == "—"
    assert known_text("Premium") == "Premium"
    assert known_text("None", "None") == "None"


def test_plan_display_falls_back_to_the_code():
    assert plan_display({"plan": "Özel"}) == "Özel"
    assert plan_display({"plan": "Unknown", "plan_code": "PREMIUM"}) == "PREMIUM"
    assert plan_display({"plan": "Unknown", "plan_code": "Unknown", "plan_type": "premium"}) == "premium"
    assert plan_display({}) == "—"


def test_price_carries_its_currency():
    assert price_label({"price": "₺149,99"}) == "₺149,99"
    assert price_label({"price": "149.99", "currency": "TRY"}) == "149.99 TRY"
    assert price_label({"price": "149.99 TRY", "currency": "TRY"}) == "149.99 TRY"
    assert price_label({"price": "Unknown", "currency": "BRL"}) == "BRL"
    assert price_label({}) == "—"


def test_premium_hit_is_counted_before_its_card_is_sent():
    """The live card must not show a fetched Premium account as free."""
    session = _session()
    session.stats.api_hits = 3
    session.stats.premium = 1
    _split_counts(session)
    assert session.stats.hits == 1
    assert session.stats.free == 2

    # A poll that claims more hits than the bot has classified yet can never
    # make `free` negative.
    session.stats.api_hits = 0
    _split_counts(session)
    assert session.stats.hits == 0
    assert session.stats.free == 0
