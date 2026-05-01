"""
Structural validator for Plausible Fiction flowcharts — Invariants 1–5.

Each invariant traces to a categorical definition in docs/pf/CT_INVARIANTS.md.
Full validation (Inv 6–11, L1–L3) lives in weve-frontend/src/lib/pf/validation/.
The backend enforces only the structural subset needed to reject corrupt persisted data.

Invariant 1  MISSING_MAPS_TO      Terminal outcomes in childFlowchart must have mapsTo
Invariant 2  INVALID_MAPS_TO      mapsTo must reference a valid enclosing-task outcome ID
Invariant 3  INVALID_NEXT_TARGET  outcome.next.task must exist in children[]
Invariant 4  ORPHANED_TASK        Every child in children[] must be reachable from an outcome
Invariant 5  MISSING_ROOT         Root must exist
"""

from __future__ import annotations
from typing import Any


def _next_task_id(next_val: Any) -> str | None:
    """Extract task ID from outcome.next (handles both dict and legacy string form)."""
    if next_val is None:
        return None
    if isinstance(next_val, str):
        return next_val
    if isinstance(next_val, dict):
        return next_val.get("task")
    return None


def _validate_node(
    task: dict[str, Any],
    parent_outcome_ids: set[str] | None,
    errors: list[str],
) -> None:
    """
    Recursively validate a TaskNode.

    parent_outcome_ids:
      None  → root level; terminals don't require mapsTo (no enclosing Kleisli arrow)
      set   → inside a childFlowchart; terminals must have mapsTo ∈ parent_outcome_ids
    """
    task_id = task.get("id", "<unknown>")
    outcomes = task.get("outcomes") or []
    # Support both "children" (TypeScript field name) and "innerFlowchart" (doc JSON name)
    children: list[dict] = task.get("children") or task.get("innerFlowchart") or []
    children_by_id = {c["id"]: c for c in children if "id" in c}

    reachable_child_ids: set[str] = set()

    for outcome in outcomes:
        oid = outcome.get("id", "<unknown>")
        next_val = outcome.get("next")
        is_terminal = next_val is None

        if is_terminal:
            # Invariant 1: terminal in childFlowchart context must have mapsTo
            if parent_outcome_ids is not None and "mapsTo" not in outcome:
                errors.append(
                    f"MISSING_MAPS_TO: terminal outcome '{oid}' in task '{task_id}' "
                    f"lacks mapsTo (CT: direction map of poly map p → free_q requires "
                    f"every leaf to map back to a direction of p)"
                )
            # Invariant 2: mapsTo must reference a valid parent outcome
            if parent_outcome_ids is not None and "mapsTo" in outcome:
                maps_to = outcome["mapsTo"]
                if maps_to not in parent_outcome_ids:
                    errors.append(
                        f"INVALID_MAPS_TO: outcome '{oid}' in task '{task_id}' has "
                        f"mapsTo='{maps_to}' which is not in enclosing task's outcomes "
                        f"{sorted(parent_outcome_ids)}"
                    )
        else:
            # Invariant 3: outcome.next.task must exist in children[]
            child_id = _next_task_id(next_val)
            if child_id is not None:
                reachable_child_ids.add(child_id)
                if child_id not in children_by_id:
                    errors.append(
                        f"INVALID_NEXT_TARGET: outcome '{oid}' in task '{task_id}' "
                        f"points to task '{child_id}' which is not in children[]"
                    )

    # Invariant 4: every child must be reachable
    for child_id in children_by_id:
        if child_id not in reachable_child_ids:
            errors.append(
                f"ORPHANED_TASK: child '{child_id}' of task '{task_id}' is not "
                f"pointed to by any outcome (CT: well-founded tree has no orphaned nodes)"
            )

    # Recurse into children (same Kleisli level → same parent_outcome_ids)
    for child in children:
        _validate_node(child, parent_outcome_ids, errors)

    # Recurse into childFlowchart (new Kleisli level → parent_outcome_ids = this task's outcomes)
    child_flowchart = task.get("childFlowchart")
    if child_flowchart and isinstance(child_flowchart, dict):
        this_outcome_ids = {o["id"] for o in outcomes if "id" in o}
        _validate_node(child_flowchart, this_outcome_ids, errors)


def validate_flowchart_structure(flowchart_json: dict[str, Any]) -> list[str]:
    """
    Run structural validation (Invariants 1–5) on a TaskNode tree.

    Returns a list of error strings. Empty list means structurally valid.
    """
    # Invariant 5: root must exist
    if not flowchart_json:
        return ["MISSING_ROOT: flowchart is null or empty"]

    errors: list[str] = []
    _validate_node(flowchart_json, parent_outcome_ids=None, errors=errors)
    return errors
