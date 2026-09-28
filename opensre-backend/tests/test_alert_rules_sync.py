"""Guard the generated vmalert manifest.

`vmalert.yaml` is generated from `alert-rules.yaml` (rule structure) and
`thresholds.env` (every tunable value) by `render.py`, because vmalert can only
read rules from a ConfigMap and a hand-maintained second copy silently drifts.

This test runs the generator's own `--check`, so it fails whenever someone
edits a threshold or a rule without re-rendering.
"""

import subprocess
import sys

from pathlib import Path

ALERTING_DIR = Path(__file__).resolve().parents[2] / "infra" / "k8s" / "alerting"
RENDER = ALERTING_DIR / "render.py"


def test_generated_vmalert_manifest_is_up_to_date():
    result = subprocess.run(
        [sys.executable, str(RENDER), "--check"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "vmalert.yaml is stale relative to alert-rules.yaml / thresholds.env\n"
        f"{result.stderr.strip()}\n"
        "Fix: python3 infra/k8s/alerting/render.py"
    )


def test_rendering_is_deterministic_and_side_effect_free():
    """--stdout must not write files, and must be stable across runs.

    Also covers the undefined-placeholder path: a typo'd ${NAME} makes
    render.py exit non-zero rather than emitting literal `${NAME}`.
    """
    runs = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(RENDER), "--stdout"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr.strip()
        runs.append(result.stdout)

    assert runs[0] == runs[1], "render.py is not deterministic"

    committed = (ALERTING_DIR / "vmalert.yaml").read_text()
    assert runs[0] == committed, (
        "rendered output differs from the committed vmalert.yaml; "
        "run: python3 infra/k8s/alerting/render.py"
    )
    assert "${" not in runs[0], "unsubstituted ${...} in rendered manifest"
