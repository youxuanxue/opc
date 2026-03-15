"""Generic output contract validators."""

from __future__ import annotations

from typing import Any


def validate_output_example(
    output_example: dict[str, Any],
    parsed_output: Any,
    *,
    value_range: dict[str, tuple[float, float]] | None = None,
) -> tuple[bool, list[str]]:
    """Validate parsed_output against output_example structure. value_range is optional for numeric fields (e.g. ai_tone_score)."""
    errors: list[str] = []
    if not isinstance(parsed_output, dict):
        return False, ["result must be JSON object"]
    for key in output_example:
        if key not in parsed_output:
            errors.append(f"missing top-level key: {key}")
    example_outputs = output_example.get("outputs")
    if isinstance(example_outputs, list) and example_outputs and isinstance(example_outputs[0], dict):
        outputs_item_keys = set(example_outputs[0])
        parsed_outputs = parsed_output.get("outputs")
        if not isinstance(parsed_outputs, list):
            errors.append("outputs must be list")
        else:
            for idx, row in enumerate(parsed_outputs):
                if not isinstance(row, dict):
                    errors.append(f"outputs[{idx}] must be object")
                    continue
                for f in outputs_item_keys:
                    if f not in row:
                        errors.append(f"outputs[{idx}] missing {f}")
    example_qc = output_example.get("quality_checks")
    if isinstance(example_qc, list) and example_qc and isinstance(example_qc[0], dict):
        qc_item_keys = set(example_qc[0])
        parsed_qc = parsed_output.get("quality_checks")
        if isinstance(parsed_qc, list):
            for idx, item in enumerate(parsed_qc):
                if isinstance(item, dict):
                    for f in qc_item_keys:
                        if f not in item:
                            errors.append(f"quality_checks[{idx}] missing {f}")
    if value_range:
        lo_hi = value_range

        def _check_value(loc: str, val: Any) -> None:
            for k, (lo, hi) in lo_hi.items():
                v = val.get(k) if isinstance(val, dict) else None
                if v is None:
                    continue
                if not isinstance(v, (int, float)):
                    errors.append(f"{loc}.{k} must be numeric")
                    continue
                if v < lo or v > hi:
                    errors.append(f"{loc}.{k} must be in [{lo}, {hi}], got {v}")

        _check_value("top_level", parsed_output)
        outputs = parsed_output.get("outputs")
        if isinstance(outputs, list):
            for idx, row in enumerate(outputs):
                if isinstance(row, dict):
                    _check_value(f"outputs[{idx}]", row)
        qc = parsed_output.get("quality_checks")
        if isinstance(qc, dict):
            _check_value("quality_checks", qc)
        elif isinstance(qc, list):
            for idx, item in enumerate(qc):
                if isinstance(item, dict):
                    _check_value(f"quality_checks[{idx}]", item)
    return len(errors) == 0, errors

