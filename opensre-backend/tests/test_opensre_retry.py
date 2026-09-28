"""Transient-failure retry around the OpenSRE agent.

The LLM step is the flakiest link: one upstream hiccup otherwise turns a
working investigation into "Unable to determine root cause", which reads like
a real conclusion rather than an infrastructure error. These tests pin down
which failures are retried and which must fail fast.
"""

import pytest

from app.services import opensre_cli


def _result(returncode, stdout="", stderr=""):
    return {"success": returncode == 0, "returncode": returncode,
            "stdout": stdout, "stderr": stderr}


TRANSIENT = [
    "LLM API request failed after multiple retries",
    "Structured diagnosis parse failed, falling back",
    "Error code: 500 - upstream error",
    "upstream returned 503",
    "rate limit reached",
    "Connection reset by peer",
    "Read timed out",
]

# Retrying these cannot help and just burns quota/time.
PERMANENT = [
    "credit_exhausted: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
    "quotaValue '20'",
    "RESOURCE_EXHAUSTED",
    "error code: 429",
    "Invalid API key provided",
    "401 Unauthorized",
]


@pytest.mark.parametrize("stderr", TRANSIENT)
def test_transient_failures_are_retried(stderr):
    assert opensre_cli._is_transient(_result(1, stderr=stderr)) is True


@pytest.mark.parametrize("stderr", PERMANENT)
def test_quota_and_auth_failures_are_not_retried(stderr):
    assert opensre_cli._is_transient(_result(1, stderr=stderr)) is False


def test_success_is_never_retried():
    assert opensre_cli._is_transient(_result(0, stdout="{}")) is False


def test_retry_stops_on_first_success(monkeypatch):
    calls = []

    def fake_run(payload, timeout=600):
        calls.append(1)
        if len(calls) == 1:
            return _result(1, stderr="LLM API request failed after multiple retries")
        return _result(0, stdout='{"root_cause": "ok"}')

    monkeypatch.setattr(opensre_cli, "_run_opensre", fake_run)
    monkeypatch.setattr(opensre_cli.time, "sleep", lambda _s: None)

    out = opensre_cli._run_opensre_with_retry({}, timeout=10, attempts=3)
    assert out["returncode"] == 0
    assert len(calls) == 2, "should stop retrying once it succeeds"


def test_retry_gives_up_after_max_attempts(monkeypatch):
    calls = []

    def fake_run(payload, timeout=600):
        calls.append(1)
        return _result(1, stderr="LLM API request failed after multiple retries")

    monkeypatch.setattr(opensre_cli, "_run_opensre", fake_run)
    monkeypatch.setattr(opensre_cli.time, "sleep", lambda _s: None)

    out = opensre_cli._run_opensre_with_retry({}, timeout=10, attempts=3)
    assert out["returncode"] == 1
    assert len(calls) == 3, "should honour the attempt cap"


def test_retry_does_not_run_for_permanent_failure(monkeypatch):
    calls = []

    def fake_run(payload, timeout=600):
        calls.append(1)
        return _result(1, stderr="credit_exhausted: quotaValue '20'")

    monkeypatch.setattr(opensre_cli, "_run_opensre", fake_run)
    monkeypatch.setattr(opensre_cli.time, "sleep", lambda _s: None)

    opensre_cli._run_opensre_with_retry({}, timeout=10, attempts=3)
    assert len(calls) == 1, "quota exhaustion must fail fast"


def test_llm_env_only_forwards_the_selected_provider():
    """A stale key for another provider must not be forwarded."""
    from app.core import config

    original = (config.settings.LLM_PROVIDER, config.settings.OPENROUTER_API_KEY,
                config.settings.GEMINI_API_KEY, config.settings.OPENROUTER_MODEL)
    try:
        config.settings.LLM_PROVIDER = "openrouter"
        config.settings.OPENROUTER_API_KEY = "or-key"
        config.settings.GEMINI_API_KEY = "gem-key"
        env = opensre_cli._llm_env()
        assert env["LLM_PROVIDER"] == "openrouter"
        assert env["OPENROUTER_API_KEY"] == "or-key"
        assert "GEMINI_API_KEY" not in env, "other provider's key must not leak"

        config.settings.LLM_PROVIDER = "gemini"
        env = opensre_cli._llm_env()
        assert env["GEMINI_API_KEY"] == "gem-key"
        assert "OPENROUTER_API_KEY" not in env
    finally:
        (config.settings.LLM_PROVIDER, config.settings.OPENROUTER_API_KEY,
         config.settings.GEMINI_API_KEY, config.settings.OPENROUTER_MODEL) = original


def test_describe_failure_names_credit_and_auth_problems():
    """A bare failure hides the cause; the message must be actionable."""
    cases = {
        "Error code: 402 - insufficient credits given your current in-flight requests":
            "insufficient credits",
        "PERMISSION_DENIED: Method doesn't allow unregistered callers":
            "rejected the API key",
        "credit_exhausted: quotaValue '20'":
            "quota exhausted",
    }
    for stderr, expected in cases.items():
        out = opensre_cli.describe_failure(_result(1, stderr=stderr))
        needle = expected.lower()
        assert needle in out["error"].lower() or needle in out["hint"].lower(), (
            f"stderr={stderr!r}\ngot: {out['error']}"
        )
        assert out["hint"], "every failure must carry a remedy"


@pytest.mark.parametrize("stderr", [
    "Error code: 402 - this key has no remaining balance",
    "PERMISSION_DENIED: unregistered callers",
])
def test_credit_and_auth_failures_are_not_retried(stderr):
    assert opensre_cli._is_transient(_result(1, stderr=stderr)) is False


def test_free_tier_in_flight_402_is_retryable():
    """OpenRouter free tier returns 402 for CONCURRENCY, not balance.

    The key still has its full limit_remaining, so backing off and retrying
    actually clears this one.
    """
    stderr = (
        "OpenRouter API failed: Error code: 402 - {'error': {'message': "
        "\"This request would exceed your available credits given your "
        "current in-flight requests. Retry after in-flight requests complete.\"}}"
    )
    assert opensre_cli._is_transient(_result(1, stderr=stderr)) is True


def test_investigations_are_serialised_by_default():
    """Concurrent agent runs are what trip the free-tier 402 in the first place."""
    assert opensre_cli._LLM_CONCURRENCY == 1
    sem = opensre_cli._llm_semaphore()
    assert sem.acquire(blocking=False) is True
    # A second concurrent run must not be handed out.
    assert opensre_cli._llm_semaphore().acquire(blocking=False) is False
    sem.release()


def test_per_request_ceiling_is_distinguished_from_empty_balance():
    """OpenRouter's 402 "can only afford N" is a per-request ceiling.

    The key still holds credit, so telling the operator to top up loses the
    real remedy: raise the key's total limit, or use a key with a higher one.
    """
    stderr = (
        "OpenRouter API failed: Error code: 402 - {'error': {'message': "
        "\"This request requires more credits, or fewer max_tokens. You "
        "requested up to 4096 tokens, but can only afford 3540. To increase, "
        "adjust the key's total limit\"}}"
    )
    out = opensre_cli.describe_failure(_result(1, stderr=stderr))
    assert "3540" in out["error"]
    assert "4096" in out["error"]
    assert "total limit" in out["hint"].lower()


def test_degenerate_rca_is_detected():
    """The CLI exits 0 when it gives up, so return code alone is not success."""
    assert opensre_cli._is_degenerate("Root cause: Unable to determine root cause") is True
    assert opensre_cli._is_degenerate("ROOT CAUSE: could not determine root cause") is True
    assert opensre_cli._is_degenerate("") is True
    assert opensre_cli._is_degenerate("Root cause: payment gateway timeouts") is False


def test_degenerate_rca_is_retried_then_reported_as_failure(monkeypatch):
    """A no-conclusion run must not be stored as a completed investigation."""
    calls = []

    def _fake(payload, timeout=600):
        calls.append(1)
        return _result(0, stdout="Root cause: Unable to determine root cause")

    monkeypatch.setattr(opensre_cli, "_run_opensre_with_retry", _fake)
    out = opensre_cli.investigate({"alertname": "A", "labels": {}, "annotations": {}})
    assert calls, "degenerate output must trigger a re-run"
    assert out["success"] is False
    assert "without identifying a root cause" in out["error"]
    assert out["hint"]


def test_degenerate_rca_that_recovers_is_a_success(monkeypatch):
    seq = [
        _result(0, stdout="Root cause: Unable to determine root cause"),
        _result(0, stdout="Root cause: payment gateway timeouts"),
    ]
    monkeypatch.setattr(opensre_cli, "_run_opensre_with_retry", lambda p, timeout=600: seq.pop(0))
    out = opensre_cli.investigate({"alertname": "A", "labels": {}, "annotations": {}})
    assert out.get("success") is not False
    assert "payment gateway" in (out.get("stdout") or "")
