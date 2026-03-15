"""External command adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def run_external_cmd(*, command: list[str], cwd: Path, timeout_sec: int = 120) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
        return {
            "command": command,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "timeout_sec": timeout_sec,
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "returncode": -9,
            "stdout": "",
            "stderr": f"command timed out after {timeout_sec}s",
            "timeout_sec": timeout_sec,
        }

