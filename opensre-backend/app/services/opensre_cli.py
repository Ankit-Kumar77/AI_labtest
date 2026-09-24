import copy
import json

from app.core.config import settings
from app.services import grounding, incident_history
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


def investigate(alert: dict, source: str = "investigation", timeout: int = 600):
    """Run `opensre investigate` with a deterministic grounding guard.

    The evidence payload is augmented with a machine-built "ground truth"
    digest and a strict grounding instruction. After the first pass the RCA is
    scanned for claims that contradict that evidence (crashes, port-bind/DNS/
    IPv6 stories, foreign systems); if any are found, ONE bounded corrective
    run is issued telling the model exactly which claims to drop. The final
    result always keeps the collected evidence available for review.
    """
    payload = copy.deepcopy(alert)
    context = grounding.build_context(payload)

    healthy = bool(context.get("healthy")) or context.get("phase") == "running"
    if healthy:
        status_rule = (
            f"[{context['target']}] is reported healthy/running in the "
            "evidence. Do NOT claim a crash, restart failure, port-bind or "
            "hostPort conflict, DNS/network resolution failure, or IPv6/AAAA "
            "problem unless the evidence explicitly shows one. DNS/AAAA error "
            "noise in CoreDNS logs ('.dns.podman', plugin errors) is an "
            "environment/upstream artifact that appears for many names - it "
            "is NOT proof the target pod's own DNS config is broken; if the "
            "target pod is Running and its own logs show no errors, say the "
            "pod is healthy and the signal is environmental DNS noise. If a "
            "container lastState shows an earlier StartError/exit-code/runtime error "
            "from a PREVIOUS attempt, treat it as HISTORICAL (the component "
            "is Running/ready now), not as a current runtime/node failure - "
            "do not frame the node, systemd, dbus, cgroups, or the container "
            "runtime as failing right now, and do not recommend "
            "cordon/drain/reboot of the node."
        )
    else:
        status_rule = (
            f"[{context['target']}] is reported as NOT healthy "
            f"(kubernetes phase: {context.get('phase') or 'unknown'}). "
            "The failure must be tied strictly to the collected signals "
            "(container restart reason, last logs, error counts, config "
            "checks). Do NOT invent a cluster-wide outage, K8s API-server "
            "unreachability, partition, DNS/SERVFAIL, IPv6, or port/network "
            "theory unless the evidence explicitly shows that signal."
        )

    payload["ground_truth"] = {
        "target": context["target"],
        "facts": context["facts"],
        "instruction": (
            "STRICT GROUNDING RULE (generated deterministically from the "
            "evidence). Produce the root cause analysis using ONLY these "
            "facts and the attached evidence. " + status_rule +
            " Do NOT mention databases, services, pods, or technologies that "
            "are absent from the evidence. If the evidence does not support a "
            "definitive root cause, say the root cause is limited to the "
            "observed signals."
        ),
    }

    result = _run_opensre(payload, timeout=timeout)

    grounding_meta = {
        "target": context["target"],
        "accurate": True,
        "flags": [],
        "self_corrected": False,
        "facts": context["facts"],
    }

    if result.get("returncode") == 0:
        rca = result.get("stdout") or ""
        flags = grounding.unsupported_claims(rca, context)
        if flags:
            # Bounded corrective loop (max 3 re-runs); stop early when the
            # report converges to the evidence.
            for _attempt in range(3):
                payload["correction_note"] = grounding.correction_instruction(flags)
                corrected = _run_opensre(payload, timeout=timeout)
                if corrected.get("returncode") != 0:
                    grounding_meta["flags"] = flags
                    grounding_meta["accurate"] = False
                    grounding_meta["note"] = (
                        "First pass contradicted the evidence and a corrective "
                        "run failed; diagnostic is unreliable."
                    )
                    break
                corrected_rca = corrected.get("stdout") or ""
                remaining = grounding.unsupported_claims(corrected_rca, context)
                result = corrected
                grounding_meta["self_corrected"] = True
                grounding_meta["flags"] = remaining
                grounding_meta["accurate"] = not remaining
                flags = remaining
                if not remaining:
                    break
    else:
        grounding_meta["note"] = ("Investigation run failed; RCA not produced. "
                                  "Evidence remains valid.")

    result["grounding"] = grounding_meta

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


def _run_opensre(alert: dict, timeout: int = 600):
    return run_command(
        [
            settings.OPENSRE_BINARY,
            "investigate",
            "--input-json",
            json.dumps(alert),
        ],
        timeout=timeout,
    )


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


def chat(prompt: dict, timeout: int = 600):
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
        ],
        timeout=timeout,
    )
