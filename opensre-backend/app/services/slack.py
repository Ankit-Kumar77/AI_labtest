"""Optional Slack webhook notifier for the alert lifecycle.

Slack is entirely optional and configured backend-side only, via the
`SLACK_WEBHOOK_URL` environment variable (supplied from a Kubernetes
Secret in-cluster). The webhook URL is never exposed to the frontend and
never written to disk.

Failures are swallowed and reported through the return value: a Slack
outage must not break alert ingestion or the investigation pipeline.
"""

import json
import os
import threading
import urllib.error
import urllib.request

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
SLACK_TIMEOUT_S = int(os.getenv("SLACK_TIMEOUT_S", "10"))
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "").strip()

SEVERITY_EMOJI = {
    "critical": "\U0001F534",
    "warning": "\U0001F7E1",
    "info": "\U0001F535",
}

# Slack rejects payloads over this size; keep digests bounded.
MAX_DESCRIPTION_CHARS = 700


def is_configured() -> bool:
    return bool(SLACK_WEBHOOK_URL)


def _blocks(alert: dict, kind: str) -> list:
    severity = (alert.get("severity") or "warning").lower()
    emoji = SEVERITY_EMOJI.get(severity, "\U0001F7E1")
    name = alert.get("alertname") or "UnknownAlert"
    namespace = alert.get("namespace") or "-"
    pod = alert.get("pod") or "-"

    if kind == "firing":
        header = f"{emoji} FIRING \u00b7 {name}"
        color = "#d40e0d" if severity == "critical" else "#daa038"
    else:
        header = f"\u2705 RESOLVED \u00b7 {name}"
        color = "#2eb886"

    description = (alert.get("summary") or alert.get("description") or "")[:MAX_DESCRIPTION_CHARS]

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header[:150], "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Namespace*\n{namespace}"},
                {"type": "mrkdwn", "text": f"*Pod*\n{pod}"},
                {"type": "mrkdwn", "text": f"*Severity*\n{severity}"},
                {"type": "mrkdwn", "text": f"*Notifications*\n{alert.get('notification_count', 1)}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": description or "_no description_"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"fingerprint `{alert.get('fingerprint', '-')}`",
                }
            ],
        },
    ]


def _payload(alert: dict, kind: str) -> dict:
    severity = (alert.get("severity") or "warning").lower()
    text = (
        f"[OpenSRE] {kind.upper()}: {alert.get('alertname')} "
        f"in {alert.get('namespace') or '-'}"
    )

    payload = {
        "text": text,
        "attachments": [
            {
                "color": "#d40e0d" if kind == "firing" else "#2eb886",
                "blocks": _blocks(alert, kind),
            }
        ],
    }

    if SLACK_CHANNEL:
        payload["channel"] = SLACK_CHANNEL

    return payload


def _post(payload: dict) -> tuple[bool, str]:
    request = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=SLACK_TIMEOUT_S) as response:
            return 200 <= response.status < 300, f"http {response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"http {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, str(exc)


def _dispatch(alert: dict, kind: str) -> None:
    """Fire the webhook off the request thread so alerts ingest fast.

    Delivery is therefore not confirmed by the return value: a Slack
    outage is logged nowhere and silently ignored, by design, so that a
    broken webhook can never block alert ingestion or the RCA pipeline.
    """
    payload = _payload(alert, kind)

    def run():
        try:
            _post(payload)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def notify_firing(alert: dict) -> dict:
    if not is_configured():
        return {"dispatched": False, "reason": "slack_not_configured"}
    _dispatch(alert, "firing")
    return {"dispatched": True, "delivered": "unknown (async)"}


def notify_resolved(alert: dict) -> dict:
    if not is_configured():
        return {"dispatched": False, "reason": "slack_not_configured"}
    _dispatch(alert, "resolved")
    return {"dispatched": True, "delivered": "unknown (async)"}


def build_payload(alert: dict, kind: str) -> dict:
    """Exposed for tests so the message shape can be asserted without network."""
    return _payload(alert, kind)
