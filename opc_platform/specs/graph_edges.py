"""Parse scenario graph edges from spec (supports [from, to] and {from, to, required_payload})."""

from __future__ import annotations

from typing import Any


def parse_edges(raw_edges: list[Any]) -> tuple[list[tuple[str, str]], dict[tuple[str, str], list[str]]]:
    """Parse edges: support [from, to] or {from, to, required_payload}.

    Returns (edges, edge_required_payload).
    """
    edges: list[tuple[str, str]] = []
    required: dict[tuple[str, str], list[str]] = {}
    for x in raw_edges or []:
        if isinstance(x, (list, tuple)) and len(x) >= 2:
            fr, to = str(x[0]), str(x[1])
            edges.append((fr, to))
            continue
        if isinstance(x, dict):
            fr = str(x.get("from") or "").strip()
            to = str(x.get("to") or "").strip()
            if fr and to:
                edges.append((fr, to))
                rp = x.get("required_payload")
                if isinstance(rp, list) and rp:
                    required[(fr, to)] = [str(k) for k in rp]
    return edges, required
