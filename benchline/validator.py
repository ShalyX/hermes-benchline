from __future__ import annotations

import csv
import io
import os
import stat
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_MAX_CSV_BYTES = 1_048_576
_MAX_ROWS = 5_000
_MAX_FINDINGS = 500
_REQUIRED_COLUMNS = ("patient_id", "specimen_id", "analyte", "result", "unit")


def _profile(analyte_units: Any, profile_source: Any) -> tuple[dict[str, dict[str, str]], str]:
    if not isinstance(profile_source, str) or not profile_source.strip():
        raise ValueError("profile_source must identify the user-supplied unit profile")
    source = profile_source.strip()
    if len(source) > 160:
        raise ValueError("profile_source must be 160 characters or fewer")
    if not isinstance(analyte_units, dict) or not analyte_units:
        raise ValueError("analyte_units must be a non-empty user-supplied object")

    units: dict[str, dict[str, str]] = {}
    for analyte, unit in analyte_units.items():
        if not isinstance(analyte, str) or not analyte.strip():
            raise ValueError("each unit profile entry needs a non-empty analyte name")
        key = analyte.strip().casefold()
        if key in units:
            raise ValueError("analyte names in the unit profile must be unique ignoring case")
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"unit profile entry for {analyte!r} needs a unit")
        units[key] = {"name": analyte.strip(), "unit": unit.strip()}
    return units, source


def _is_finite_number(value: str) -> bool:
    try:
        number = Decimal(value.strip())
    except (InvalidOperation, ValueError):
        return False
    return number.is_finite()


def validate_csv_text(
    csv_text: str,
    analyte_units: dict[str, str],
    profile_source: str,
) -> dict[str, Any]:
    """Preflight one numeric-result CSV; do not interpret values clinically."""
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise ValueError("CSV content is empty")
    if len(csv_text.encode("utf-8")) > _MAX_CSV_BYTES:
        raise ValueError("CSV exceeds the 1 MiB safety limit")

    units, source = _profile(analyte_units, profile_source)
    csv_text = csv_text.removeprefix("\ufeff")
    try:
        reader = csv.DictReader(io.StringIO(csv_text, newline=""))
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row")
        headers = [name.strip().casefold() if name else "" for name in reader.fieldnames]
        if len(headers) != len(set(headers)):
            raise ValueError("CSV contains duplicate column names")
        missing = [column for column in _REQUIRED_COLUMNS if column not in headers]
        if missing:
            raise ValueError("CSV is missing required columns: " + ", ".join(missing))

        findings: list[dict[str, Any]] = []
        issue_counts: dict[str, int] = {}
        seen_result_keys: set[tuple[str, str]] = set()
        seen_rows: set[int] = set()
        rows_seen = data_quality = 0
        findings_truncated = False

        for row_number, raw_row in enumerate(reader, start=2):
            if row_number - 1 > _MAX_ROWS:
                raise ValueError(f"CSV exceeds the {_MAX_ROWS:,} row safety limit")
            if None in raw_row:
                raise ValueError(f"CSV row {row_number} has more values than the header")
            row = {
                (key or "").strip().casefold(): (value or "").strip()
                for key, value in raw_row.items()
            }
            rows_seen += 1
            patient_id = row.get("patient_id", "")
            specimen_id = row.get("specimen_id", "")
            analyte = row.get("analyte", "")
            analyte_key = analyte.casefold()
            result_text = row.get("result", "")
            observed_unit = row.get("unit", "")
            row_had_finding = False

            def add(issue: str, **details: Any) -> None:
                nonlocal data_quality, findings_truncated, row_had_finding
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
                data_quality += 1
                row_had_finding = True
                if len(findings) < _MAX_FINDINGS:
                    finding = {"row": row_number, "issue": issue}
                    finding.update(details)
                    findings.append(finding)
                else:
                    findings_truncated = True

            if not patient_id or not specimen_id:
                add(
                    "missing_identifier",
                    missing_fields=[
                        field
                        for field, value in (("patient_id", patient_id), ("specimen_id", specimen_id))
                        if not value
                    ],
                )

            result_key = (specimen_id, analyte_key)
            if specimen_id and analyte and result_key in seen_result_keys:
                add("duplicate_result_key")
            elif specimen_id and analyte:
                seen_result_keys.add(result_key)

            if not analyte:
                add("missing_analyte")
            elif analyte_key not in units:
                add("unknown_analyte")
            elif observed_unit != units[analyte_key]["unit"]:
                add("unit_mismatch")

            if not result_text:
                add("missing_result")
            elif not _is_finite_number(result_text):
                add("non_numeric_result")

            if row_had_finding:
                seen_rows.add(row_number)

        if rows_seen == 0:
            raise ValueError("CSV contains no data rows")

    except csv.Error as exc:
        raise ValueError(f"CSV parsing failed: {exc}") from None

    return {
        "ok": True,
        "summary": {
            "rows_seen": rows_seen,
            "rows_with_findings": len(seen_rows),
            "data_quality_findings": data_quality,
            "finding_counts": issue_counts,
        },
        "findings_truncated": findings_truncated,
        "findings": findings,
        "scope": "Mechanical CSV structure, finite-number, duplicate-key and exact-unit checks only; no clinical value or range interpretation.",
    }


def validate_csv_file(
    path_text: str,
    analyte_units: dict[str, str],
    profile_source: str,
) -> dict[str, Any]:
    """Read one explicitly selected CSV locally; never write or return identifiers/values."""
    if not isinstance(path_text, str) or not path_text.strip():
        raise ValueError("path must identify one CSV file")
    path = Path(path_text).expanduser()
    if path.suffix.casefold() != ".csv":
        raise ValueError("selected file must have a .csv extension")
    if path.is_symlink():
        raise ValueError("symbolic-link CSV paths are not accepted")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise ValueError("CSV could not be opened; check that the selected file exists and is readable") from None

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("selected CSV must be a regular file")
        if info.st_size > _MAX_CSV_BYTES:
            raise ValueError("CSV exceeds the 1 MiB safety limit")
        chunks = bytearray()
        while len(chunks) <= _MAX_CSV_BYTES:
            chunk = os.read(fd, min(65_536, _MAX_CSV_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > _MAX_CSV_BYTES:
            raise ValueError("CSV exceeds the 1 MiB safety limit")
    finally:
        os.close(fd)

    try:
        content = bytes(chunks).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV must be encoded as UTF-8") from None
    return validate_csv_text(content, analyte_units, profile_source)
