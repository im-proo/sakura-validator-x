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


def test_broken_zip_is_left_to_the_api(tmp_path):
    """A zip we cannot open must still be submitted, not rejected."""
    path = tmp_path / "broken.zip"
    path.write_bytes(b"not a zip at all")
    assert file_service.count_netflix_cookies(str(path), "zip") is None
    assert file_service.estimate_cookie_count(str(path), "zip") >= 1


def test_renamed_zip_is_left_to_the_api(tmp_path):
    """A zip renamed to .txt is sniffed and handed to the checker."""
    real = tmp_path / "real.zip"
    with zipfile.ZipFile(real, "w") as archive:
        archive.writestr("a.txt", "#HttpOnly_.netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tv\n")
    renamed = tmp_path / "cookies.txt"
    renamed.write_bytes(real.read_bytes())

    assert file_service.resolve_kind(str(renamed), "txt") == "zip"
    # Sniffed as a zip, so it is read properly instead of being called empty.
    assert file_service.count_netflix_cookies(str(renamed), "txt") == 1
    assert file_service.estimate_cookie_count(str(renamed), "txt") >= 1


def test_zip_counts_httponly_cookie_sets(tmp_path):
    path = tmp_path / "cookies.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "one.txt",
            "#HttpOnly_.netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tAAA\n"
            "#HttpOnly_.netflix.com\tTRUE\t/\tTRUE\t0\tSecureNetflixId\tBBB\n",
        )
        archive.writestr(
            "two.txt",
            ".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tCCC\n"
            ".netflix.com\tTRUE\t/\tTRUE\t0\tSecureNetflixId\tDDD\n",
        )
    assert file_service.count_netflix_cookies(str(path), "zip") == 2


def test_zip_with_nested_archive_is_left_to_the_api(tmp_path):
    """A zip holding another archive cannot be judged here."""
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("a.txt", ".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tv\n")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("inner.zip", inner.read_bytes())
    assert file_service.count_netflix_cookies(str(outer), "zip") is None


def test_zip_without_netflix_is_a_certain_zero(tmp_path):
    path = tmp_path / "junk.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("notes.txt", "hello there\nnothing to see\n")
    assert file_service.count_netflix_cookies(str(path), "zip") == 0


def test_plain_text_without_netflix_is_a_certain_zero(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("just some notes\n", encoding="utf-8")
    assert file_service.count_netflix_cookies(str(path), "txt") == 0


def test_text_mentioning_netflix_without_id_is_left_to_the_api(tmp_path):
    path = tmp_path / "odd.txt"
    path.write_text('{"netflix": {"session": "opaque"}}\n', encoding="utf-8")
    assert file_service.count_netflix_cookies(str(path), "txt") is None


def test_rar_is_never_counted_locally(tmp_path):
    path = tmp_path / "cookies.rar"
    path.write_bytes(b"Rar!\x1a\x07\x00payload")
    assert file_service.count_netflix_cookies(str(path), "rar") is None


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


def test_hit_text_handles_go_api_types():
    """The Go API sends cookie as a CookieSet object and remaining as ints."""
    hit = {
        "account": {
            "email": "go@example.com",
            "remaining_hours": 3,
            "remaining_minutes": 7,
            "email_verified": "no",
            "extra_member": "yes",
            "hold_status": "no",
        },
        "cookie": {"netflix_id": "NID", "secure_netflix_id": "SNID", "raw": ""},
    }
    text = file_service.build_hit_text(hit)
    assert "NetflixId=NID" in text
    assert "SecureNetflixId=SNID" in text
    assert "Remaining: 3h 07m" in text
    assert "Email Verified: No" in text
    assert "Extra Member: Yes" in text
    assert "Hold Status: No" in text


def test_premium_classification_uses_go_plan_fields():
    from models.schemas import is_premium_hit

    assert is_premium_hit({"account": {"plan": "Standard", "plan_type": "premium"}})
    assert is_premium_hit({"account": {"plan": "Premium"}})
    assert is_premium_hit({"account": {"plan": "Ultimate"}})
    assert not is_premium_hit({"account": {"plan": "Premium", "is_ads_tier": True}})
    assert not is_premium_hit({"account": {"plan": "Standard", "plan_type": "standard"}})


def test_premium_bundle_file_names():
    assert file_service.bundle_file_names("zip") == "premium_accounts.zip"
    assert file_service.bundle_file_names("txt") == "premium_accounts.txt"
