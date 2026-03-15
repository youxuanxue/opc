"""Cursor CLI adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

DEFAULT_CURSOR_AGENT_PATH = "/Users/xuejiao/.local/bin/agent"


def _run_command(command: list[str], cwd: Path, timeout_sec: int = 30) -> dict[str, Any]:
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
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": -9,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": f"command timed out after {timeout_sec}s",
            "timeout_sec": timeout_sec,
        }


def run_cursor_cli(
    *,
    workspace: Path,
    prompt: str,
    merged_inputs: dict[str, Any],
    runtime_cfg: dict[str, Any],
) -> dict[str, Any]:
    executable = str(
        runtime_cfg.get("executable_path")
        or merged_inputs.get("cursor_agent_path")
        or DEFAULT_CURSOR_AGENT_PATH
    )
    command = [
        executable,
        "-p",
        prompt,
        "--output-format",
        str(runtime_cfg.get("output_format") or "json"),
        "--workspace",
        str(workspace),
    ]
    trust = runtime_cfg.get("trust")
    if trust is None:
        trust = bool(merged_inputs.get("cursor_agent_trust", True))
    if bool(trust):
        command.append("--trust")
    timeout_sec = int(runtime_cfg.get("timeout_sec") or merged_inputs.get("cursor_agent_timeout_sec") or 90)
    return _run_command(command=command, cwd=workspace, timeout_sec=timeout_sec)

