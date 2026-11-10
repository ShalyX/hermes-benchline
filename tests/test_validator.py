import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from benchline.validator import validate_csv_file, validate_csv_text


ROOT = Path(__file__).resolve().parents[1]
PROFILE_SOURCE = "Synthetic unit profile v1; not clinical"
UNIT_PROFILE = {"marker_alpha": "unit_a", "marker_beta": "unit_b"}


class ValidateCsvTextTests(unittest.TestCase):
    def test_flags_only_csv_quality_and_unit_issues(self):
        csv_text = (
            "patient_id,specimen_id,analyte,result,unit\n"
            "PRIVATE-1,S-001,marker_alpha,22.0,unit_a\n"
            "PRIVATE-2,S-002,marker_alpha,14.0,wrong_unit\n"
            "PRIVATE-3,S-003,unlisted,5,unit_x\n"
            "PRIVATE-4,S-004,marker_beta,not-a-number,unit_b\n"
        )

        report = validate_csv_text(csv_text, UNIT_PROFILE, PROFILE_SOURCE)

        self.assertEqual(report["summary"], {
            "rows_seen": 4,
            "rows_with_findings": 3,
            "data_quality_findings": 3,
            "finding_counts": {
                "unit_mismatch": 1,
                "unknown_analyte": 1,
                "non_numeric_result": 1,
            },
        })
        self.assertNotIn("profile_source", report)
        self.assertNotIn(PROFILE_SOURCE, str(report))
        self.assertEqual([finding["issue"] for finding in report["findings"]], [
            "unit_mismatch", "unknown_analyte", "non_numeric_result"
        ])
        self.assertNotIn("PRIVATE", str(report))
        self.assertNotIn("S-001", str(report))
        self.assertNotIn("22.0", str(report))
        self.assertNotIn("marker_alpha", str(report))
        self.assertNotIn("wrong_unit", str(report))
        self.assertNotIn("configured_range", str(report))

    def test_reports_missing_and_duplicate_keys_without_echoing_identifiers(self):
        csv_text = (
            "patient_id,specimen_id,analyte,result,unit\n"
            "P1,S-1,marker_alpha,5,unit_a\n"
            "P2,S-1,marker_alpha,5,unit_a\n"
            ",S-2,marker_alpha,6,unit_a\n"
        )
        report = validate_csv_text(csv_text, UNIT_PROFILE, PROFILE_SOURCE)
        self.assertEqual(report["summary"]["data_quality_findings"], 2)
        self.assertEqual([item["issue"] for item in report["findings"]], [
            "duplicate_result_key", "missing_identifier"
        ])
        self.assertNotIn("S-1", str(report))

    def test_normalizes_header_case_whitespace_and_utf8_bom(self):
        csv_text = "\ufeff Patient_ID , Specimen_ID , Analyte , Result , Unit \nP,S,marker_alpha,5,unit_a\n"
        report = validate_csv_text(csv_text, UNIT_PROFILE, PROFILE_SOURCE)
        self.assertEqual(report["summary"]["rows_seen"], 1)
        self.assertEqual(report["findings"], [])

    def test_rejects_unknown_or_invalid_unit_profile_source(self):
        csv_text = "patient_id,specimen_id,analyte,result,unit\nP,S,marker_alpha,5,unit_a\n"
        for profile, source in (({}, PROFILE_SOURCE), ({"x": "unit"}, ""), ({"x": ""}, PROFILE_SOURCE)):
            with self.subTest(profile=profile, source=source), self.assertRaises(ValueError):
                validate_csv_text(csv_text, profile, source)

    def test_rejects_bad_structure_and_empty_data(self):
        with self.assertRaisesRegex(ValueError, "required columns"):
            validate_csv_text("patient_id,analyte,result\nP,x,1\n", UNIT_PROFILE, PROFILE_SOURCE)
        with self.assertRaisesRegex(ValueError, "duplicate column"):
            validate_csv_text("patient_id,specimen_id,analyte,result,unit, UNIT \nP,S,x,1,u,u\n", UNIT_PROFILE, PROFILE_SOURCE)
        with self.assertRaisesRegex(ValueError, "no data rows"):
            validate_csv_text("patient_id,specimen_id,analyte,result,unit\n", UNIT_PROFILE, PROFILE_SOURCE)

    def test_caps_csv_rows_and_report_findings(self):
        header = "patient_id,specimen_id,analyte,result,unit\n"
        too_many = header + "P,S,marker_alpha,1,unit_a\n" * 5001
        with self.assertRaisesRegex(ValueError, "5,000 row"):
            validate_csv_text(too_many, UNIT_PROFILE, PROFILE_SOURCE)

        many_findings = header + "".join(
            f",S-{index},marker_alpha,1,unit_a\n" for index in range(600)
        )
        report = validate_csv_text(many_findings, UNIT_PROFILE, PROFILE_SOURCE)
        self.assertEqual(len(report["findings"]), 500)
        self.assertTrue(report["findings_truncated"])
        self.assertEqual(report["summary"]["rows_with_findings"], 600)
        self.assertEqual(report["summary"]["data_quality_findings"], 600)

    def test_file_reader_is_read_only_and_hides_identifiers_and_values(self):
        csv_text = "patient_id,specimen_id,analyte,result,unit\nPRIVATE,S-1,marker_alpha,not-numeric,unit_a\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.csv"
            path.write_text(csv_text, encoding="utf-8")
            before = path.stat().st_mtime_ns
            report = validate_csv_file(str(path), UNIT_PROFILE, PROFILE_SOURCE)
            self.assertEqual(path.read_text(encoding="utf-8"), csv_text)
            self.assertEqual(path.stat().st_mtime_ns, before)
            self.assertNotIn("PRIVATE", str(report))
            self.assertNotIn("not-numeric", str(report))
            self.assertEqual(report["findings"][0]["issue"], "non_numeric_result")

    def test_file_reader_rejects_non_csv_symlink_and_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_path = root / "results.txt"
            text_path.write_text("not a csv", encoding="utf-8")
            csv_path = root / "results.csv"
            csv_path.write_text("patient_id,specimen_id,analyte,result,unit\nP,S,marker_alpha,5,unit_a\n", encoding="utf-8")
            link_path = root / "linked.csv"
            link_path.symlink_to(csv_path)
            directory_csv = root / "directory.csv"
            directory_csv.mkdir()
            for path in (text_path, link_path):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    validate_csv_file(str(path), UNIT_PROFILE, PROFILE_SOURCE)
            with self.assertRaisesRegex(ValueError, "regular file"):
                validate_csv_file(str(directory_csv), UNIT_PROFILE, PROFILE_SOURCE)

    def test_file_reader_enforces_byte_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.csv"
            path.write_bytes(b"x" * (1_048_576 + 1))
            with self.assertRaisesRegex(ValueError, "1 MiB"):
                validate_csv_file(str(path), UNIT_PROFILE, PROFILE_SOURCE)


class PluginRegistrationTests(unittest.TestCase):
    def test_register_exposes_callable_tool_and_returns_deterministic_report(self):
        spec = importlib.util.spec_from_file_location(
            "benchline_plugin_test",
            ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)],
        )
        if spec is None or spec.loader is None:
            self.fail("Could not create the plugin package spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules["benchline_plugin_test"] = module
        spec.loader.exec_module(module)

        class FakeContext:
            def __init__(self):
                self.tools = {}

            def register_tool(self, **kwargs):
                self.tools[kwargs["name"]] = kwargs

        ctx = FakeContext()
        module.register(ctx)
        self.assertEqual(set(ctx.tools), {"benchline_validate_csv"})
        tool = ctx.tools["benchline_validate_csv"]
        self.assertEqual(tool["toolset"], "benchline")
        self.assertEqual(tool["schema"]["required"], ["path", "analyte_units", "profile_source"])
        output = tool["handler"]({
            "path": str(ROOT / "demo" / "synthetic-results.csv"),
            "analyte_units": {"marker_alpha": "unit_a", "marker_beta": "unit_b"},
            "profile_source": "Synthetic demo profile v1",
        })
        report = json.loads(output)
        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"]["rows_seen"], 5)
        self.assertNotIn("Synthetic demo profile v1", output)
        self.assertNotIn("profile_source", report)
        self.assertNotIn("PSEUDO-001", output)


if __name__ == "__main__":
    unittest.main()
