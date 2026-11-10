# Benchline

## Install from GitHub

Install directly from GitHub:

```bash
hermes plugins install ShalyX/hermes-benchline --enable
```

After the catalog entry is accepted, users can also install by catalog name:

```bash
hermes plugins install benchline --enable
```

The catalog entry is not accepted yet; catalog-name installation will not work until then.

Benchline is a native Hermes plugin for one focused workflow: locally preflight a *numeric-result CSV export* before a lab operator hands it off to another system. The operator chooses a single file, supplies the lab-owned analyte/unit map and its source/version, and gets a deterministic, row-based QC report.

## What it checks

- required columns and duplicate column headers
- missing patient/specimen identifiers (the identifiers themselves are never returned)
- duplicate `(specimen_id, analyte)` rows
- missing, malformed, or non-finite numeric results
- analytes absent from the supplied profile
- exact unit-label mismatches (no conversion or inference)

The report is a deterministic QC summary; it may include aggregate row/issue counts, affected row numbers, issue codes, missing schema-field names and a scope note. It does **not** return patient/specimen identifiers, analyte or unit labels, profile-source label, or measured values. The model can use row numbers to guide review in the source file. It checks file structure and transfer consistency only; it does **not** evaluate whether a numeric result is high, low, normal, critical or clinically significant.

## Scope and prior art

The current upstream Hermes catalog includes `health-insights` (v0.3.2, pinned at `d6d09c1f17ada6ca60368a1e0c7a7b5bd4efefc6`), which analyzes Apple Health/HealthRelay data and includes lab-result workflows. It operates through a separate CLI and portable skills against a local health database; it can retain imported lab data and send printed values to the configured model/provider. Benchline is narrower: a one-file numeric CSV preflight that checks structure and exact unit labels, without trends, interpretation, persistence or database access. The live catalog directory contains no `benchline.yaml` (the direct upstream path returned 404). This is a meaningful workflow distinction, but the overlap is real and the catalog maintainer still decides whether it is sufficiently distinct.

## Safety and privacy

- Reads one explicitly selected `.csv` file, at most 1 MiB / 5,000 rows; symlinks and non-regular files are rejected.
- No network calls, subprocesses, writes, durable storage or background work.
- The input CSV contents are read locally and are not echoed in the tool result. The profile-source label is also not returned. However, the selected path, unit map, and profile-source label are tool-call arguments visible to the session's configured model/provider; do not use this workflow if those details are sensitive.
- The bounded QC report is also returned to the session and therefore visible to its configured model/provider.
- The operator supplies the analyte/unit map and a short source/version label for each run. Benchline ships no clinical reference intervals.
- Findings are not release authorization. A qualified operator must review source rows and correct issues in the lab's approved system.
- Supports numeric-result exports only; qualitative panels and nonstandard schemas are out of scope for this MVP.

## CSV contract

Headers are case-insensitive and surrounding whitespace is ignored:

```csv
patient_id,specimen_id,analyte,result,unit
PSEUDO-001,SYN-001,marker_alpha,8.0,unit_a
PSEUDO-002,SYN-002,marker_beta,4.0,unit_b
```

The tool call also supplies a run-specific map and provenance label:

```json
{
  "analyte_units": {"marker_alpha": "unit_a", "marker_beta": "unit_b"},
  "profile_source": "Synthetic demo unit profile v1"
}
```

`demo/` contains synthetic identifiers and generic units only. It is not a clinical fixture.

## Development

Python 3.10+; runtime uses only the standard library. Linux is the only platform tested for this draft.

```bash
python -m unittest discover -s tests -v
```

Hermes source at `1400400907b0259b0f9865358950d9bf6ea28184` was used directly to run the current admission validator and Plugin Doctor against the public Benchline repository. Admission validation passed 15 checks with zero warnings; Plugin Doctor passed runtime registration in a temporary isolated `HERMES_HOME`. The installed Hermes 0.19.0 CLI does not expose those newer subcommands; the current source implementations were called directly.

## License

MIT. See `LICENSE`.
