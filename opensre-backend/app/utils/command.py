import os
import subprocess


def run_command(command, timeout=180, env_overrides=None):
    # Start from the current environment so PATH/HOME survive, then overlay
    # the caller's overrides. Subprocess would inherit os.environ anyway, but
    # being explicit keeps the LLM provider deterministic no matter how the
    # backend process was started.
    env = None
    if env_overrides:
        env = {**os.environ, **{k: v for k, v in env_overrides.items() if v}}

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
        }
