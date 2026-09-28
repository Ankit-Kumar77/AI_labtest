from app.services import llm_ceiling_proxy as proxy


def test_clamp_caps_requests_at_the_current_limit():
    proxy.CAP = 3500
    assert proxy.clamp_tokens(4096) == 3500
    assert proxy.clamp_tokens(1200) == 1200
    assert proxy.clamp_tokens(0) == 3500
    assert proxy.clamp_tokens(None) == 3500
    assert proxy.clamp_tokens("nonsense") == 3500


def test_affordable_cap_reads_the_provider_ceiling():
    message = (
        "This request requires more credits, or fewer max_tokens. You "
        "requested up to 4096 tokens, but can only afford 3540. To increase, "
        "adjust the key's total limit"
    )
    assert proxy.affordable_cap(message) == 3540 - proxy.AFFORD_MARGIN


def test_affordable_cap_ignores_unrelated_errors():
    for message in (
        "This request would exceed your available credits given your current in-flight requests.",
        "No auth credentials found",
        "",
    ):
        assert proxy.affordable_cap(message) is None


def test_affordable_cap_never_drops_below_the_floor():
    assert proxy.affordable_cap("can only afford 10") == proxy.MIN_TOKENS


def test_proxy_forwards_and_rewrites_the_model(monkeypatch):
    proxy.CAP = 3500
    proxy.MODEL_REWRITE = "openai/gpt-4o-mini"
    seen = {}

    def _fake_post(path, payload, api_key):
        seen["path"] = path
        seen["payload"] = payload
        seen["key"] = api_key
        return 200, json_bytes({"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr(proxy, "_post", _fake_post)
    proxy.API_KEY = "real-key"

    handler = _handler()
    status, raw = _drive(handler, {"model": "gpt-4o-mini", "max_tokens": 4096}, path="/v1/chat/completions")

    assert status == 200
    assert seen["path"] == "/chat/completions"
    assert seen["payload"]["max_tokens"] == 3500
    assert seen["payload"]["model"] == "openai/gpt-4o-mini"
    assert seen["key"] == "real-key"
    assert b"OK" in raw


def test_proxy_lowers_the_cap_and_replays_on_a_ceiling_error(monkeypatch):
    monkeypatch.setattr(proxy, "CAP", 4096)
    monkeypatch.setattr(proxy, "MODEL_REWRITE", "openai/gpt-4o-mini")
    calls = []

    def _fake_post(path, payload, api_key):
        calls.append(payload["max_tokens"])
        if payload["max_tokens"] >= 4096:
            return 402, json_bytes(
                {"error": {"message": "You requested up to 4096 tokens, but can only afford 3540."}}
            )
        return 200, json_bytes({"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr(proxy, "_post", _fake_post)
    status, raw = _drive(_handler(), {"model": "m", "max_tokens": 4096}, path="/v1/chat/completions")

    assert status == 200
    assert calls[0] == 4096, "the first attempt must try the CLI's real value"
    assert calls[1] == 3540 - proxy.AFFORD_MARGIN
    assert proxy.CAP == 3540 - proxy.AFFORD_MARGIN
    assert b"OK" in raw


def test_proxy_does_not_lower_the_cap_for_an_in_flight_402(monkeypatch):
    """An in-flight rejection is concurrency, not a token ceiling.

    The cap must not shrink, or repeated in-flight rejections would ratchet
    max_tokens down to nothing over time.
    """
    monkeypatch.setattr(proxy, "CAP", 3500)
    monkeypatch.setattr(proxy, "INFLIGHT_RETRIES", 0)
    monkeypatch.setattr(proxy, "MODEL_REWRITE", "openai/gpt-4o-mini")
    calls = []

    def _fake_post(path, payload, api_key):
        calls.append(payload["max_tokens"])
        return 402, json_bytes(
            {"error": {"message": "would exceed your available credits given your current in-flight requests"}}
        )

    monkeypatch.setattr(proxy, "_post", _fake_post)
    status, raw = _drive(_handler(), {"model": "m", "max_tokens": 3000}, path="/v1/chat/completions")

    assert status == 402
    assert proxy.CAP == 3500, "an in-flight 402 must not change the cap"
    assert all(token == 3000 for token in calls)


def json_bytes(obj):
    import json

    return json.dumps(obj).encode()


class _FakeWFile:
    def __init__(self):
        self.buffer = b""

    def write(self, chunk):
        self.buffer += chunk


def _handler():
    """Minimal handler wired to in-memory request/response buffers."""

    class _H(proxy.Handler):
        def __init__(self):
            self._headers = {}
            self.wfile = _FakeWFile()
            self._status = None
            self._len = 0

        def _stub(self, name, *a, **kw):
            return None

    handler = _H()
    handler.send_response = lambda status, *a, **kw: setattr(handler, "_status", status)
    handler.send_header = lambda name, value: (
        handler._headers.__setitem__(name, value)
        if name == "content-length"
        else None
    )
    handler.end_headers = lambda *a, **kw: None
    return handler


def _drive(handler, payload, path="/v1/chat/completions"):
    import io
    import json

    raw = json.dumps(payload).encode()
    handler.headers = {"content-length": str(len(raw))}
    handler.path = path
    handler.rfile = io.BytesIO(raw)
    handler.do_POST()
    return handler._status, handler.wfile.buffer


def test_transport_errors_become_a_retryable_503(monkeypatch):
    """Pod egress blips (Errno 101 after start) must not drop the connection."""
    import urllib.error

    def _boom(request, timeout=None, context=None):
        raise urllib.error.URLError("[Errno 101] Network is unreachable")

    monkeypatch.setattr(proxy.urllib.request, "urlopen", _boom)
    status, raw = proxy._post("/chat/completions", {"model": "m"}, "key")

    assert status == 503
    assert b"upstream unreachable" in raw


def test_ipv6_candidates_are_dropped():
    """Kind has no IPv6 route, so an AAAA candidate fails with ENETUNREACH."""
    import socket as sock

    fake = [
        (sock.AF_INET6, sock.SOCK_STREAM, 6, "", ("2606:4700::1", 443, 0, 0)),
        (sock.AF_INET, sock.SOCK_STREAM, 6, "", ("104.18.2.115", 443)),
    ]
    patched = proxy._real_getaddrinfo
    try:
        proxy._real_getaddrinfo = lambda *a, **k: fake
        # The import-time hook replaced socket.getaddrinfo with the wrapper.
        assert sock.getaddrinfo("openrouter.ai", 443) == [fake[1]]
    finally:
        proxy._real_getaddrinfo = patched


def test_ipv4_only_filter_keeps_results_when_no_ipv4_exists():
    import socket as sock

    v6_only = [(sock.AF_INET6, sock.SOCK_STREAM, 6, "", ("2606:4700::1", 443, 0, 0))]
    patched = proxy._real_getaddrinfo
    try:
        proxy._real_getaddrinfo = lambda *a, **k: v6_only
        assert sock.getaddrinfo("v6only.example", 443) == v6_only
    finally:
        proxy._real_getaddrinfo = patched


def test_inflight_402_is_waited_out(monkeypatch):
    """A free-tier in-flight rejection clears on its own, so retry with backoff."""
    monkeypatch.setattr(proxy, "CAP", 1024)
    monkeypatch.setattr(proxy, "INFLIGHT_RETRIES", 3)
    monkeypatch.setattr(proxy, "MODEL_REWRITE", "openai/gpt-4o-mini")
    slept = []
    monkeypatch.setattr(proxy.time, "sleep", lambda s: slept.append(s))
    calls = []

    def _fake_post(path, payload, api_key):
        calls.append(1)
        if len(calls) < 3:
            return 402, json_bytes(
                {"error": {"message": "would exceed your available credits given your current in-flight requests"}}
            )
        return 200, json_bytes({"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr(proxy, "_post", _fake_post)
    status, raw = _drive(_handler(), {"model": "m", "max_tokens": 1000}, path="/v1/chat/completions")

    assert status == 200
    assert len(calls) == 3
    assert slept == [1, 2], "backoff should grow between attempts"


def test_inflight_retries_are_bounded(monkeypatch):
    monkeypatch.setattr(proxy, "CAP", 1024)
    monkeypatch.setattr(proxy, "INFLIGHT_RETRIES", 2)
    monkeypatch.setattr(proxy, "MODEL_REWRITE", "openai/gpt-4o-mini")
    monkeypatch.setattr(proxy.time, "sleep", lambda s: None)
    calls = []

    def _fake_post(path, payload, api_key):
        calls.append(1)
        return 402, json_bytes({"error": {"message": "in-flight requests"}})

    monkeypatch.setattr(proxy, "_post", _fake_post)
    status, raw = _drive(_handler(), {"model": "m", "max_tokens": 1000}, path="/v1/chat/completions")

    assert status == 402
    assert len(calls) == 3, "initial attempt plus INFLIGHT_RETRIES retries"
