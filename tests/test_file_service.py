"""Cookie intake limits and hit artefacts."""

from __future__ import annotations

import asyncio
import os
import zipfile

import pytest

from services import file_service

SAMPLE_HIT = {
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
        "profiles": ["one", "two"],
        "user_guid": "ABC123",
        "valid_till_utc": "2026-11-14 04:53:16 UTC",
        "remaining": "0h 58m 59s",
    },
    "cookie": "NetflixId=abc; SecureNetflixId=def",
}


def test_extension_detection():
    assert file_service.extension_of("cookies.TXT") == "txt"
    assert file_service.extension_of("bundle.zip") == "zip"
    assert file_service.extension_of("noext") == ""


def test_estimate_counts_netflix_cookie_sets(tmp_path):
    path = tmp_path / "cookies.txt"
    lines = []
    for _ in range(10):
        lines.extend([".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tv"] * 4)
    path.write_text("\n".join(lines), encoding="utf-8")
    assert file_service.estimate_cookie_count(str(path), "txt") == 10


def test_estimate_falls_back_to_line_count(tmp_path):
    path = tmp_path / "plain.txt"
    path.write_text("line1\nline2\nline3\n", encoding="utf-8")
    assert file_service.estimate_cookie_count(str(path), "txt") == 3


def test_empty_file_is_rejected(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")
    with pytest.raises(file_service.InvalidFileError):
        file_service.estimate_cookie_count(str(path), "txt")


def test_broken_zip_is_rejected(tmp_path):
    path = tmp_path / "broken.zip"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(file_service.InvalidFileError):
        file_service.estimate_cookie_count(str(path), "zip")


def test_hit_text_contains_every_documented_field():
    text = file_service.build_hit_text(SAMPLE_HIT)
    for expected in (
        "NETFLIX HIT",
        "Name: Lisaan",
        "Email: user@example.com",
        "Country: IN",
        "Plan: Premium",
        "Member Since: June 2026",
        "Next Billing: 2026-11-20",
        "Payment: UPI",
        "Quality: UHD",
        "Streams: 4",
        "Profiles: one, two",
        "User GUID: ABC123",
        "Valid Till (UTC): 2026-11-14 04:53:16 UTC (Remaining: 0h 58m 59s)",
        "NetflixId=abc",
    ):
        assert expected in text, expected


def test_hit_text_uses_dashes_instead_of_none():
    hit = {"account": {"email": "x@y.z", "card": None, "phone": "None"}, "cookie": "c"}
    text = file_service.build_hit_text(hit)
    assert "Card: —" in text
    assert "Phone: —" in text


def test_hit_file_name_is_branded():
    assert file_service.hit_file_name(SAMPLE_HIT) == "sakura_hit_user@example.com.txt"


def test_bundle_creates_txt_and_zip():
    bundle = asyncio.run(file_service.build_hits_bundle([SAMPLE_HIT], token="testrun"))
    try:
        assert bundle["count"] == 1
        assert os.path.exists(bundle["txt"])
        assert os.path.exists(bundle["zip"])

        with open(bundle["txt"], encoding="utf-8") as handle:
            body = handle.read()
        assert "𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗" in body
        assert "user@example.com" in body

        with zipfile.ZipFile(bundle["zip"]) as archive:
            names = archive.namelist()
        assert file_service.bundle_file_names("txt") in names
        assert any(name.startswith("sakura_hit_") for name in names)
    finally:
        file_service.cleanup(bundle.get("txt"), bundle.get("zip"))


def test_empty_bundle_is_a_no_op():
    bundle = asyncio.run(file_service.build_hits_bundle([]))
    assert bundle == {"txt": None, "zip": None, "count": 0}


def test_cleanup_ignores_missing_paths():
    file_service.cleanup(None, "", "/tmp/definitely-not-here-sakura.txt")
