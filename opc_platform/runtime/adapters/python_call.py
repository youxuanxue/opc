"""Python adapter placeholder for in-process callable execution."""

from __future__ import annotations

from typing import Any


def run_python_call(*, callable_ref: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "callable_ref": callable_ref,
        "kwargs": kwargs,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }

