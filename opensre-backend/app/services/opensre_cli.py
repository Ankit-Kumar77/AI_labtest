import json

from app.core.config import settings
from app.services import incident_history
from app.utils.command import run_command


def version():
    result = run_command(
        [
            settings.OPENSRE_BINARY,
            "--version",
        ]
    )

    if not result.get("success") and "No such file or directory" in result.get(
        "stderr", ""
    ):
        return {
            "success": False,
            "installed": False,
            "error": "OpenSRE CLI is not installed",
        }

    return result


def doctor():
    return run_command(
        [
            settings.OPENSRE_BINARY,
            "doctor",
        ]
    )


def status():
    return run_command(
        [
            settings.OPENSRE_BINARY,
            "status",
        ]
    )


def onboard():
    return run_command(
        [
            settings.OPENSRE_BINARY,
            "onboard",
        ]
    )


def investigate(alert: dict, source: str = "investigation"):
    result = run_command(
        [
            settings.OPENSRE_BINARY,
            "investigate",
            "--input-json",
            json.dumps(alert),
        ]
    )
    if result.get("returncode") != 0:
        failure = describe_failure(result)
        result["error"] = failure["error"]
        result["hint"] = failure["hint"]
    # Persist to incident history so analyses stay accessible from the
    # Incident Report page after the run (best-effort, never breaks the
    # investigation itself).
    saved = incident_history.save(alert, result, source=source)
    if saved.get("id"):
        result["incident_id"] = saved["id"]
    return result


def describe_failure(result: dict) -> dict:
    """User-friendly error + remediation for a failed `opensre investigate`.

    Evidence collection happens in the backend before the CLI runs, so a
    CLI failure never means "no data" — the evidence payload in the same
    API response is still valid and reviewable.
    """
    stderr = (result.get("stderr") or "") + "\n" + (result.get("stdout") or "")
    lowered = stderr.lower()
    if (
        "credit exhaust" in lowered
        or "quota" in lowered
        or "resource_exhausted" in lowered
        or "error code: 429" in lowered
    ):
        return {
            "error": (
                "OpenSRE LLM quota exhausted (Gemini free-tier allows "
                "20 requests/day). Evidence was collected successfully — "
                "only the AI summary failed."
            ),
            "hint": (
                "Wait for the daily quota reset, or switch provider "
                "(`opensre auth login openrouter` and set OPENROUTER_MODEL "
                "to an available model, or change LLM_PROVIDER), then "
                "re-run Investigate. The evidence in this response is "
                "still valid for manual review."
            ),
        }
    if "nonetype" in lowered or "traceback" in lowered:
        return {
            "error": (
                "OpenSRE agent crashed while generating the RCA. "
                "Evidence was collected successfully and is included "
                "in this response."
            ),
            "hint": (
                "Re-run Investigate (transient agent errors are common); "
                "if it persists, inspect the evidence payload manually "
                "or run `opensre doctor` to check agent capabilities "
                "(note: raw network requests are blocked in this "
                "environment, so RCA relies on the attached evidence)."
            ),
        }
    return {
        "error": (
            "OpenSRE investigation failed. Evidence was collected "
            "successfully and is included in this response."
        ),
        "hint": (
            "Check `opensre doctor` / `opensre auth status` for provider "
            "issues, then re-run Investigate."
        ),
    }


def chat(prompt: dict):
    context = prompt.get("context", {})

    alert = {
        "alertname": "OpenSREWebChat",
        "status": "firing",
        "severity": "info",
        "labels": {
            "cluster": context.get("cluster"),
            "namespace": context.get("namespace"),
            "pod": context.get("pod"),
        },
        "annotations": {
            "summary": "OpenSRE Web Chat",
            "description": prompt.get("message", ""),
        },
    }

    return run_command(
        [
            settings.OPENSRE_BINARY,
            "investigate",
            "--input-json",
            json.dumps(alert),
        ]
    )
