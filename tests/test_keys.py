"""Key format and helper behaviour (Section 6)."""

from __future__ import annotations

import re

from services import key_service

FORMAT = re.compile(r"^SAKURA-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")


def test_generated_keys_match_the_documented_format():
    for _ in range(50):
        key = key_service.generate_key()
        assert FORMAT.match(key), key
        assert key_service.is_valid_format(key)


def test_generated_keys_are_unique():
    keys = {key_service.generate_key() for _ in range(300)}
    assert len(keys) == 300


def test_key_alphabet_avoids_ambiguous_characters():
    assert not set("IO01") & set(key_service.KEY_ALPHABET)
    for key in (key_service.generate_key() for _ in range(50)):
        body = key.split("-", 1)[1].replace("-", "")
        assert not set("IO01") & set(body)


def test_normalise_accepts_loose_input():
    assert key_service.normalise_key(" sakura-abcd-efgh-ijkl ") == "SAKURA-ABCD-EFGH-IJKL"
    assert key_service.normalise_key("ABCD-EFGH-IJKL") == "SAKURA-ABCD-EFGH-IJKL"
    assert key_service.normalise_key("") == ""


def test_format_validation_rejects_junk():
    assert key_service.is_valid_format("SAKURA-ABCD-EFGH-IJKL")
    assert not key_service.is_valid_format("SAKURA-ABC-EFGH-IJKL")
    assert not key_service.is_valid_format("PROMO-ABCD-EFGH-IJKL")
    assert not key_service.is_valid_format("sa kura")


def test_receipt_ids_are_unique_and_prefixed():
    ids = {key_service.new_receipt_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(value.startswith("RCP-") for value in ids)


def test_sakura_is_not_key_grantable():
    import asyncio

    try:
        asyncio.run(key_service.generate_keys("sakura", 30, 1, 1))
    except ValueError as exc:
        assert "basic or premium" in str(exc)
    else:  # pragma: no cover - the guard must hold
        raise AssertionError("sakura keys must not be generated")
