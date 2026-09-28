"""OpenAI-compatible shim that keeps LLM calls under a provider's token ceiling.

Why this exists
---------------
The OpenSRE CLI asks every provider for a fixed 4096 output tokens. It exposes
no environment variable or config key for that value (verified: `config set
max_tokens`, `OPENAI_BASE_URL` and friends are all ignored), so on a provider
that cannot afford a 4096-token request every investigation fails with:

    HTTP 402 - "You requested up to 4096 tokens, but can only afford 3540."

That is a *per-request* ceiling, not a depleted balance: raising the key's
total limit fixes it at the provider, but nothing in this repo can change it.

This shim sits between the CLI and the provider. The CLI talks OpenAI protocol
to `http://127.0.0.1:<port>/v1` via the CLI's `custom-openai` provider; the
shim forwards to the real provider, clamping `max_tokens` to an affordable
value and injecting the real credentials.

The cap is self-tuning: on a "can only afford N" rejection the shim lowers the
cap to N (minus a margin) and replays the request once. If the key is later
funded, the cap simply rises back toward the CLI's requested value, so the shim
degrades to a pass-through and can be removed without any other change.

It is stdlib-only on purpose: the shim shares the pod with the backend and must
not add a dependency to the application image.
"""

import json
import logging
import os
import socket
import time
import ssl
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("llm_ceiling_proxy")

# A provider hostname commonly resolves to IPv4 *and* IPv6. A Kind cluster has
# no IPv6 route, so the IPv6 candidate fails with ENETUNREACH ("Network is
# unreachable") and roughly every other request dies even though the IPv4
# addresses are perfectly reachable. Drop IPv6 candidates for this process.
if os.getenv("LLM_CEILING_PROXY_IPV4_ONLY", "1") not in ("0", "false", "False"):
    _real_getaddrinfo = socket.getaddrinfo

    def _getaddrinfo(host, port, *args, **kwargs):
        infos = _real_getaddrinfo(host, port, *args, **kwargs)
        v4 = [info for info in infos if info[0] == socket.AF_INET]
        return v4 or infos

    socket.getaddrinfo = _getaddrinfo

# Ask for at least this few tokens when clamping, so a run never degrades into
# one-token answers that the agent cannot use.
MIN_TOKENS = 256
# Headroom kept below whatever the provider says it can afford, so a replay is
# not rejected a second time for landing exactly on the boundary.
AFFORD_MARGIN = 64
# How many times to wait out a free-tier "in-flight" rejection before giving up.
INFLIGHT_RETRIES = int(os.getenv("LLM_CEILING_PROXY_INFLIGHT_RETRIES", "4"))

UPSTREAM = os.getenv("LLM_CEILING_PROXY_UPSTREAM", "https://openrouter.ai/api/v1").rstrip("/")
API_KEY = os.getenv("LLM_CEILING_PROXY_API_KEY", "")
MODEL_REWRITE = os.getenv("LLM_CEILING_PROXY_MODEL", "").strip()
# Start optimistic: the first rejection reveals the real ceiling.
CAP = int(os.getenv("LLM_CEILING_PROXY_MAX_TOKENS", "4096"))


def clamp_tokens(requested):
    """Clamp a requested token count to the currently affordable cap."""
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        return CAP
    if requested <= 0 or requested > CAP:
        return CAP
    return requested


def affordable_cap(message):
    """Extract a new cap from a "...can only afford N" provider error.

    Returns None when the error is not a per-request ceiling, so unrelated
    failures are surfaced untouched.
    """
    if "can only afford" not in message:
        return None
    digits = ""
    marker = message.lower().find("can only afford")
    for char in message[marker:]:
        if char.isdigit():
            digits += char
        elif digits:
            break
    if not digits:
        return None
    return max(MIN_TOKENS, int(digits) - AFFORD_MARGIN)


def _error_message(raw):
    """Best-effort provider error message, tolerating non-JSON bodies."""
    try:
        return json.loads(raw).get("error", {}).get("message", "")
    except (ValueError, AttributeError):
        return raw.decode("utf-8", "replace")


def _post(path, payload, api_key):
    url = f"{UPSTREAM}{path}"
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=300, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        # Pod egress occasionally blips (Errno 101 right after start). Report it
        # as a retryable status instead of dropping the connection, so the CLI's
        # own retry logic can see it.
        logger.warning("upstream transport error: %s", exc)
        return 503, json.dumps(
            {"error": {"message": f"upstream unreachable: {exc}", "type": "transport_error"}}
        ).encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, raw):
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        # The CLI probes /v1/models during auth checks.
        raw = json.dumps({"object": "list", "data": [{"id": MODEL_REWRITE or "local"}]}).encode()
        self._send(200, raw)

    def do_POST(self):
        global CAP
        length = int(self.headers.get("content-length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, json.dumps({"error": {"message": "invalid JSON"}}).encode())
            return

        if MODEL_REWRITE:
            payload["model"] = MODEL_REWRITE
        if "max_tokens" in payload:
            payload["max_tokens"] = clamp_tokens(payload["max_tokens"])

        path = self.path
        if path.startswith("/v1/"):
            path = path[3:]

        status, raw = _post(path, payload, API_KEY)

        # Self-tune: a ceiling rejection tells us the affordable value exactly.
        if status == 402:
            message = _error_message(raw)
            new_cap = affordable_cap(message)
            if new_cap is not None and new_cap < CAP:
                previous = CAP
                CAP = new_cap
                logger.warning(
                    "provider can afford %s tokens, was capped at %s; replaying",
                    new_cap,
                    previous,
                )
                if "max_tokens" in payload:
                    payload["max_tokens"] = clamp_tokens(payload.get("max_tokens", CAP))
                status, raw = _post(path, payload, API_KEY)

        # The free tier also rejects overlapping in-flight requests, and the
        # agent loop issues calls back to back. This is the one provider
        # signal that clears on its own, so wait it out here rather than
        # making the whole investigation fail.
        for attempt in range(INFLIGHT_RETRIES):
            message = _error_message(raw)
            if status != 402 or "in-flight" not in message.lower():
                break
            delay = min(2 ** attempt, 20)
            logger.warning("in-flight 402; waiting %ss (attempt %s)", delay, attempt + 1)
            time.sleep(delay)
            status, raw = _post(path, payload, API_KEY)

        self._send(status, raw)

    def log_message(self, *args):
        pass


def main():
    port = int(os.getenv("LLM_CEILING_PROXY_PORT", "8900"))
    if not API_KEY:
        raise SystemExit("LLM_CEILING_PROXY_API_KEY is required")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger.info("listening on 127.0.0.1:%s -> %s (cap=%s)", port, UPSTREAM, CAP)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
