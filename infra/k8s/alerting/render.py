#!/usr/bin/env python3
"""Render vmalert.yaml from alert-rules.yaml + thresholds.env.

Why this exists
---------------
vmalert can only read rules from a ConfigMap, not a file, so the rules have to
be embedded in its manifest. That previously meant hand-maintaining the same
eight rules in two places, and a silent edit to only one would ship rules that
no longer matched the documented behaviour. Keeping the Deployment half in a
template and generating the ConfigMap half removes the second copy entirely,
and makes every threshold configurable from one file.

Usage
-----
    python3 infra/k8s/alerting/render.py            # write vmalert.yaml
    python3 infra/k8s/alerting/render.py --check    # non-zero if stale
    LATENCY_HIGH_SECONDS=0.5 python3 .../render.py  # one-off override

Only ${UPPER_SNAKE} placeholders are substituted, so Prometheus alert-template
syntax ({{ $labels.pod }}) passes through untouched.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from pathlib import Path

HERE = Path(__file__).resolve().parent
# Rule sources live in rules/ so that `kubectl apply -f infra/k8s/alerting/`
# only ever sees real manifests, not a raw Prometheus rules file.
RULES = HERE / "rules" / "alert-rules.yaml"
THRESHOLDS = HERE / "rules" / "thresholds.env"
TEMPLATE = HERE / "rules" / "vmalert.deployment.yaml"
OUTPUT = HERE / "vmalert.yaml"

PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def load_thresholds() -> dict[str, str]:
    """thresholds.env values, overridden by the process environment."""
    values: dict[str, str] = {}
    for raw in THRESHOLDS.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    values.update({k: v for k, v in os.environ.items() if k in values})
    return values


def render() -> str:
    rules = RULES.read_text()
    thresholds = load_thresholds()

    unknown = set(PLACEHOLDER.findall(rules)) - set(thresholds)
    if unknown:
        raise SystemExit(
            "alert-rules.yaml references undefined thresholds: "
            + ", ".join(sorted(unknown))
            + "\nAdd them to thresholds.env"
        )

    def sub(match: re.Match[str]) -> str:
        return thresholds[match.group(1)]

    rendered_rules = PLACEHOLDER.sub(sub, rules)

    if "${" in rendered_rules:
        raise SystemExit("unsubstituted ${...} left in rendered rules")

    header = (
        "# GENERATED FILE - do not edit by hand.\n"
        "# Source:     alert-rules.yaml (rule structure)\n"
        "# Thresholds: thresholds.env    (every tunable value)\n"
        "# Regenerate: python3 infra/k8s/alerting/render.py\n"
        "---\n"
    )
    return (
        header
        + "apiVersion: v1\n"
        + "kind: ConfigMap\n"
        + "metadata:\n"
        + "  name: opensre-alert-rules\n"
        + "  namespace: observability\n"
        + "data:\n"
        + "  alert-rules.yaml: |\n"
        + "".join(
            ("    " + line if line.strip() else "") + "\n"
            for line in rendered_rules.rstrip("\n").split("\n")
        )
        + "\n"
        + TEMPLATE.read_text().lstrip("\n")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if vmalert.yaml is out of date instead of rewriting it",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the rendered manifest instead of writing it (never mutates)",
    )
    args = parser.parse_args()

    content = render()

    if args.stdout:
        sys.stdout.write(content)
        return 0

    if args.check:
        if not OUTPUT.exists():
            print(f"{OUTPUT.name} is missing; run render.py", file=sys.stderr)
            return 1
        current = OUTPUT.read_text()
        if current != content:
            print(
                f"{OUTPUT.name} is STALE - it no longer matches "
                f"{RULES.name} + {THRESHOLDS.name}.\n"
                "Run: python3 infra/k8s/alerting/render.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT.name} is up to date")
        return 0

    OUTPUT.write_text(content)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
