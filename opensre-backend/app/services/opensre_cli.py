import copy
import json
import os
import re
import threading
import time

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


import threading


# One agent run at a time. The default LLM provider is on a free tier that
# rejects overlapping in-flight requests (HTTP 402); serialising here keeps a
# burst of simultaneous alerts from starving the provider.
_LLM_CONCURRENCY = int(os.getenv("OPENSRE_MAX_CONCURRENT_INVESTIGATIONS", "1"))
_llm_slots = threading.BoundedSemaphore(max(1, _LLM_CONCURRENCY))


def _llm_semaphore():
    return _llm_slots


def _is_transient(result: dict) -> bool:
    """True when the CLI failed for a reason a retry can plausibly fix.

    Provider-side hiccups (rate limits, upstream 5xx, the agent's structured
    diagnosis failing to parse) surface as a non-zero exit with no usable
    RCA. Retrying those is worth it; retrying a quota exhaustion or a genuine
    crash just wastes time, so those are deliberately excluded.
    """
    if result.get("returncode") == 0:
        return False
    text = ((result.get("stderr") or "") + "\n" + (result.get("stdout") or "")).lower()

    # OpenRouter's free tier answers HTTP 402 with "...would exceed your
    # available credits given your current in-flight requests. Retry after
    # in-flight requests complete." That is a CONCURRENCY limit, not an empty
    # balance (the key still has its full limit_remaining), so backing off and
    # retrying genuinely fixes it. A 402 without that wording is a real
    # balance problem and must not be retried.
    if "402" in text and "in-flight" in text:
        return True

    if any(
        marker in text
        for marker in (
            "credit exhaust",
            "quota",
            "resource_exhausted",
            "error code: 429",
            "error code: 402",
            "insufficient credit",
            "available credits",
            "permission_denied",
            "unregistered callers",
            "unauthorized",
            "invalid api key",
        )
    ):
        return False
    return any(
        marker in text
        for marker in (
            "api request failed",
            "structured diagnosis parse failed",
            "rate limit",
            "429",
            "500",
            "502",
            "503",
            "504",
            "overloaded",
            "timeout",
            "timed out",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
        )
    )


def _run_opensre_with_retry(payload: dict, timeout: int = 600, attempts: int = 4):
    """Run the agent, retrying only transient provider failures.

    Runs are serialised through a semaphore: the OpenRouter free tier rejects
    concurrent in-flight requests with HTTP 402, and the backend triggers one
    investigation per firing alert, so several alerts firing at once used to
    guarantee that failure.
    """
    delay = 5
    with _llm_semaphore():
        result = _run_opensre(payload, timeout=timeout)
        for _attempt in range(1, attempts):
            if not _is_transient(result):
                return result
            time.sleep(delay)
            result = _run_opensre(payload, timeout=timeout)
            # A free-tier 402 clears as in-flight requests drain, so allow a
            # longer cool-off than a generic upstream blip needs.
            delay = min(delay * 2, 60) if "in-flight" in (
                (result.get("stderr") or "") + (result.get("stdout") or "")
            ).lower() else min(delay * 2, 30)
    return result


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

    result = _run_opensre_with_retry(payload, timeout=timeout)

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
                corrected = _run_opensre_with_retry(payload, timeout=timeout)
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

    # The CLI exits 0 even when it gives up, so a run that produced no root
    # cause would otherwise be stored as a completed investigation and read as
    # a real (and wrong) conclusion. Retry once, then fail it honestly.
    if result.get("returncode") == 0 and _is_degenerate(result.get("stdout") or ""):
        retry = _run_opensre_with_retry(payload, timeout=timeout)
        if retry.get("returncode") == 0 and not _is_degenerate(
            retry.get("stdout") or ""
        ):
            result = retry
            grounding_meta["self_corrected"] = True
            result["grounding"] = grounding_meta
        else:
            if retry.get("returncode") != 0:
                failure = describe_failure(retry)
                result["error"] = failure["error"]
                result["hint"] = failure["hint"]
            else:
                result["error"] = (
                    "OpenSRE finished without identifying a root cause "
                    "(return code 0, no conclusion)."
                )
                result["hint"] = (
                    "The agent ran but produced no root cause, usually a "
                    "transient structured-output parse failure. Evidence was "
                    "collected and is included here; re-run the investigation."
                )
            result["success"] = False

    if result.get("returncode") != 0 and not result.get("error"):
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


_DEGENERATE_RCA = (
    "unable to determine root cause",
    "unable to determine the root cause",
    "root cause: unknown",
    "could not determine root cause",
    "insufficient evidence to determine",
)


def _is_degenerate(rca: str) -> bool:
    """True when the report has no real conclusion.

    The agent exits 0 in these cases, so success has to be judged from the
    report text, not the return code.
    """
    lowered = (rca or "").strip().lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in _DEGENERATE_RCA)


def _llm_env():
    """Provider env for the OpenSRE CLI subprocess.

    Only the selected provider's credentials are forwarded, so a stale
    GEMINI_API_KEY can't silently win over the configured provider.
    """
    provider = (settings.LLM_PROVIDER or "").strip().lower()
    if provider == "openrouter":
        return {
            "LLM_PROVIDER": provider,
            "OPENROUTER_API_KEY": settings.OPENROUTER_API_KEY,
            "OPENROUTER_MODEL": settings.OPENROUTER_MODEL,
        }
    if provider == "custom-openai":
        # Used with the in-pod token-ceiling shim: the CLI keeps talking
        # OpenAI protocol to a local address, and the shim forwards to the
        # real provider. The key here is a placeholder; the shim holds the
        # real credential and never exposes it to the CLI.
        return {
            "LLM_PROVIDER": provider,
            "CUSTOM_OPENAI_API_KEY": settings.CUSTOM_OPENAI_API_KEY or "via-shim",
            "CUSTOM_OPENAI_BASE_URL": settings.CUSTOM_OPENAI_BASE_URL,
            "CUSTOM_OPENAI_MODEL": settings.CUSTOM_OPENAI_MODEL,
        }
    if provider == "gemini":
        return {"LLM_PROVIDER": provider, "GEMINI_API_KEY": settings.GEMINI_API_KEY}
    return {"LLM_PROVIDER": provider} if provider else {}


def _run_opensre(alert: dict, timeout: int = 600):
    return run_command(
        [
            settings.OPENSRE_BINARY,
            "investigate",
            "--input-json",
            json.dumps(alert),
        ],
        timeout=timeout,
        env_overrides=_llm_env(),
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
        or "error code: 402" in lowered
        or "insufficient credit" in lowered
        or "available credits" in lowered
        or "permission_denied" in lowered
        or "unregistered callers" in lowered
    ):
        provider = settings.LLM_PROVIDER or "unset"

        if "402" in lowered or "insufficient credit" in lowered or "available credits" in lowered:
            affordable = re.search(r"can only afford (\d+)", lowered)
            requested = re.search(r"requested up to (\d+) tokens", lowered)
            if affordable:
                # Not an empty balance: the key holds credit, but not enough
                # to cover ONE request of the size OpenSRE asks for. The only
                # fix is on the provider side.
                want = requested.group(1) if requested else "4096"
                headline = (
                    f"OpenSRE LLM provider '{provider}' refused a single "
                    f"request: the key can afford only ~{affordable.group(1)} "
                    f"output tokens but OpenSRE requests {want} (HTTP 402)."
                )
                remedy = (
                    "This is a per-request ceiling, not a depleted balance, and "
                    "OpenSRE's token limit is not configurable. Raise the "
                    "provider key's total limit (OpenRouter: Settings -> API "
                    "keys -> edit the key's limit, or add credits to the "
                    "workspace) so one call can afford the full request, or "
                    "switch LLM_PROVIDER to a key with a higher limit."
                )
            else:
                headline = (
                    f"OpenSRE LLM provider '{provider}' rejected the request: "
                    "insufficient credits (HTTP 402)."
                )
                remedy = (
                    "Top up the balance for this API key, or point LLM_PROVIDER "
                    "at a provider with quota. An investigation makes several "
                    "LLM calls, so the key needs enough credit to cover a full run."
                )
        elif "permission_denied" in lowered or "unregistered callers" in lowered:
            headline = (
                f"OpenSRE LLM provider '{provider}' rejected the API key "
                "(HTTP 403 PERMISSION_DENIED)."
            )
            remedy = (
                "The configured key is not valid for this provider/model. "
                "Re-issue the key and update the opensre-backend-secrets "
                "Secret."
            )
        else:
            headline = (
                f"OpenSRE LLM quota exhausted for provider '{provider}'"
                + (
                    " (the Gemini free tier allows only ~20 requests/day)"
                    if provider == "gemini"
                    else ""
                )
                + "."
            )
            remedy = (
                "The backend defaults to OpenRouter (LLM_PROVIDER=openrouter) "
                "because the Gemini free tier is too small for an "
                "alert-driven pipeline. Check that OPENROUTER_API_KEY is set "
                "and OPENROUTER_MODEL is a model your key can reach."
            )

        return {
            "error": (
                headline
                + " Evidence was collected successfully — only the AI "
                "summary failed."
            ),
            "hint": remedy + (
                " The evidence in this response is still valid for manual "
                "review, and the alert lifecycle itself is unaffected."
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
