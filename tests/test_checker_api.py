"""Checker client shape: URL handling and typed failures (Section 10)."""

from __future__ import annotations

import asyncio

import pytest

from core import checker_api


def test_api_prefix_is_documented_value():
    assert checker_api.API_PREFIX == "/api/v1"


def test_base_url_normalisation():
    assert checker_api._base("https://nf-01.example.com") == "https://nf-01.example.com"
    assert checker_api._base("https://nf-01.example.com/") == "https://nf-01.example.com"
    assert checker_api._base("https://nf-01.example.com/api/v1") == "https://nf-01.example.com"
    assert checker_api._base("  https://x/api/v1/  ") == "https://x"


def test_error_hierarchy():
    assert issubclass(checker_api.CheckerUnavailable, checker_api.CheckerAPIError)
    assert issubclass(checker_api.CheckerAPIError, Exception)


def test_short_collapses_whitespace():
    assert checker_api._short("a\n\n b\t c", 10) == "a b c"


def run_isolated(coro_factory):
    """Run one call in its own loop and always close the shared session."""

    async def _runner():
        try:
            return await coro_factory()
        finally:
            await checker_api.close_session()

    return asyncio.run(_runner())


def test_ping_health_marks_unreachable_hosts_offline():
    status, latency = run_isolated(
        lambda: checker_api.ping_health("http://127.0.0.1:9")
    )
    assert status == "offline"
    assert latency == 0.0


def test_submit_job_surfaces_unreachable_host(tmp_path):
    upload = tmp_path / "cookies.txt"
    upload.write_text(".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tv\n", encoding="utf-8")

    with pytest.raises(checker_api.CheckerUnavailable):
        run_isolated(
            lambda: checker_api.submit_job_file(
                "http://127.0.0.1:9", str(upload), "cookies.txt", concurrency=100
            )
        )


def test_cancel_job_never_raises():
    result = run_isolated(lambda: checker_api.cancel_job("http://127.0.0.1:9", "job-1"))
    assert result == {"status": "unknown"}
