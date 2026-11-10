"""Benchline native Hermes plugin registration."""

from __future__ import annotations

import json
import logging
from typing import Any

from .benchline.validator import validate_csv_file

logger = logging.getLogger(__name__)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path to one explicitly selected local .csv file. Read-only; maximum "
                "1 MiB and 5,000 data rows. Tool arguments are visible to the configured model/provider; "
                "do not call if the path is sensitive. Do not call without the user's selected file."
            ),
        },
        "analyte_units": {
            "type": "object",
            "description": (
                "Explicit per-run analyte-to-unit allowlist from the lab's own export profile. "
                "This is only an exact label check; no reference ranges or clinical thresholds. "
                "Tool inputs are visible to the configured model/provider; use only non-sensitive labels."
            ),
            "minProperties": 1,
            "additionalProperties": {"type": "string", "minLength": 1},
        },
        "profile_source": {
            "type": "string",
            "minLength": 1,
            "maxLength": 160,
            "description": (
                "Human-readable source/version of the user-supplied unit profile. "
                "Tool inputs are visible to the configured model/provider; use only a non-sensitive label."
            ),
        },
    },
    "required": ["path", "analyte_units", "profile_source"],
    "additionalProperties": False,
}


def _validate_csv(args: dict[str, Any], **kwargs: Any) -> str:
    """Run local deterministic preflight; never return identifiers or measured values."""
    try:
        report = validate_csv_file(
            args.get("path"),
            args.get("analyte_units"),
            args.get("profile_source"),
        )
        return json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    except Exception:
        logger.exception("Benchline preflight failed")
        return json.dumps({"ok": False, "error": "Preflight failed unexpectedly; no report was produced."})


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="benchline_validate_csv",
        toolset="benchline",
        schema=_TOOL_SCHEMA,
        handler=_validate_csv,
    )
