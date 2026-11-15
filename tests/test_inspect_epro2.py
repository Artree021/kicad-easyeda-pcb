# SPDX-License-Identifier: MIT
"""Synthetic tests; run with --work-dir pointing inside a project work folder.

No real board, account, EDA session or network access is used. Temporary cases
are created below the requested work folder and removed after each test.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_epro2.py"
SPEC = importlib.util.spec_from_file_location("inspect_epro2", SCRIPT)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)
WORK_ROOT = None


def record(kind, identity=None, payload=None, ticket=1):
    header = {"type": kind, "ticket": ticket}
    if identity is not None:
        header["id"] = identity
    body = "" if payload is None else json.dumps(payload, ensure_ascii=False)
    return json.dumps(header) + "||" + body + "|"


def dochead(identity="synthetic-board", kind="PCB"):
    return record("DOCHEAD", payload={
        "docType": kind, "uuid": identity,
        "creator": "synthetic-private-marker", "title": "synthetic-title-marker",
    })


def board_text():
    return "\n".join([
        dochead(),
        record("COMPONENT", "component-a", {
            "x": 1.25, "y": 2.5, "angle": 90, "layerId": 1,
            "attrs": {"Designator": "U1", "FootprintName": "old-library-name",
                      "Footprint": "old-local-doc", "3D Model": "synthetic-model",
                      "3D Model Transform": [1, 1, 1, 90, 0, 0, 0, 0, 0]},
        }),
        record("ATTR", "attribute-a", {
            "parentId": "component-a", "key": "Footprint", "value": "active-local-doc",
        }),
        record("LAYER_PHYS", "layer-a", {"thickness": 1.4}),
        record("LAYER_PHYS", "layer-b", {"thickness": 60.0}),
        record("POUR", "pour-a", {"layerId": 1, "net": "GND"}),
        record("POURED", "poured-a", {"pourId": "pour-a", "path": [[0, 0], [4, 0], [4, 4]]}),
        record("LINE", "line-a", {"width": 0.2, "net": "GND", "layerId": 1}),
    ])


def private_board_text(directory="SyntheticPrivate"):
    """Synthetic privacy markers only; no host paths or account data."""
    return "\n".join([
        dochead(),
        record("COMPONENT", "private-component", {"attrs": {
            "Designator": "U1",
            "Footprint": "Z:\\" + directory + "\\models\\sensor.step",
            "FootprintName": "Z:\\" + directory + "\\library\\sensor.step",
            "3D Model": "/home/synthetic-private/model.step",
            "3D Model Transform": {"nested": [
                "uri=file:///synthetic-private/local/model.step",
                "model at /home/synthetic-private/model.step",
                "模型：/home/synthetic-private/model.step",
                "[/home/synthetic-private/model.step]",
                "模型：/root",
            ]},
        }}),
    ])


class NativeInventoryTests(unittest.TestCase):
    def setUp(self):
        if WORK_ROOT is None:
            raise RuntimeError("Run this test file with --work-dir")
        self.temporary = tempfile.TemporaryDirectory(prefix="inventory-case-", dir=WORK_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def archive(self, text=None, name="synthetic.epro2", members=1):
        destination = self.work / name
        with zipfile.ZipFile(destination, "w") as archive:
            for number in range(members):
                archive.writestr(f"synthetic-{number}.epru", board_text() if text is None else text)
        return destination

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, args)],
            cwd=self.work, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )

    def test_read_only_inventory_hash_and_private_header_omission(self):
        source = self.archive()
        before = source.read_bytes()
        report = HELPER.inspect(source)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(report["source_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(report["source_name"], source.name)
        self.assertEqual(report["documents"][0]["counts"]["LINE"], 1)
        self.assertEqual(report["documents"][0]["pours"], {"POUR": 1, "POURED": 1, "FILL": 0})
        self.assertIn("INVENTORY_ONLY", report["status"])
        rendered = json.dumps(report)
        self.assertNotIn("synthetic-private-marker", rendered)
        self.assertNotIn("synthetic-title-marker", rendered)

    def test_hash_and_inventory_use_one_source_read(self):
        source = self.archive()
        original = Path.read_bytes
        reads = []

        def tracked_read(path):
            reads.append(path)
            return original(path)

        with patch.object(Path, "read_bytes", tracked_read):
            HELPER.inspect(source)
        self.assertEqual(reads, [source])

    def test_revisions_and_tombstones_resolve_by_type_and_id(self):
        text = "\n".join([
            dochead(),
            record("LINE", "shared-id", {"width": 0.1}),
            record("LINE", "shared-id", {"width": 0.3}, ticket=2),
            record("VIA", "shared-id", {"diameter": 0.6}),
            record("VIA", "remove-me", {"diameter": 0.6}),
            record("VIA", "remove-me"),
        ])
        document = HELPER.parse_native(text)[0]
        self.assertEqual(document["revisions"], 1)
        self.assertEqual(document["tombstones"], 1)
        self.assertEqual(len(document["records"]), 2)
        widths = [value["width"] for header, value in document["records"].values()
                  if header["type"] == "LINE"]
        self.assertEqual(widths, [0.3])

    def test_duplicate_document_history_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Line 2.*duplicate document identity"):
            HELPER.parse_native(dochead() + "\n" + dochead())

    def test_malformed_or_unscoped_records_are_rejected(self):
        cases = [
            ("", "No documents"),
            (record("LINE", "a", {"width": 0.2}), "precedes first DOCHEAD"),
            (dochead() + "\n" + record("LINE"), "tombstone lacks record id"),
            (dochead() + "\n" + '{"type":"LINE","id":"a"}||[]|', "non-object payload"),
            (dochead() + "\n" + '{"type":"LINE","id":"a"}||{}|extra', "unexpected data"),
            ('{"type":"DOCHEAD"}|{}|', "expected \\|\\|"),
            (record("DOCHEAD", payload={"docType": "PCB"}), "document type/UUID missing"),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                HELPER.parse_native(text)

    def test_effective_footprint_property_overrides_embedded_and_legacy_name(self):
        component = HELPER.inspect(self.archive())["documents"][0]["components"][0]
        self.assertEqual(component["footprint"], "active-local-doc")
        self.assertEqual(component["footprint_name"], "old-library-name")
        self.assertEqual(component["model_uuid"], "synthetic-model")
        text = "\n".join([dochead(), record("COMPONENT", "component-b", {
            "attrs": {"Designator": "R1", "FootprintName": "legacy-only"},
        })])
        fallback = HELPER.inspect(self.archive(text, "fallback.epro2"))["documents"][0]["components"][0]
        self.assertEqual(fallback["footprint"], "legacy-only")

    def test_thickness_requires_explicit_unit(self):
        source = self.archive()
        unknown = HELPER.inspect(source)["documents"][0]
        self.assertNotIn("thickness_mm", unknown["physical_layers"][0])
        self.assertNotIn("sum_of_all_physical_layer_thicknesses_mm", unknown)
        mil = HELPER.inspect(source, "mil")["documents"][0]
        self.assertAlmostEqual(mil["physical_layers"][0]["thickness_mm"], 1.4 * 0.0254)
        self.assertAlmostEqual(mil["sum_of_all_physical_layer_thicknesses_mm"], 61.4 * 0.0254)
        mm = HELPER.inspect(source, "mm")["documents"][0]
        self.assertEqual(mm["physical_layers"][0]["thickness_mm"], 1.4)
        with self.assertRaisesRegex(ValueError, "Thickness unit"):
            HELPER.inspect(source, "inch")

    def test_nonnumeric_thickness_is_not_silently_converted(self):
        text = "\n".join([dochead(), record("LAYER_PHYS", "layer-a", {"thickness": True})])
        document = HELPER.inspect(self.archive(text), "mil")["documents"][0]
        self.assertNotIn("thickness_mm", document["physical_layers"][0])
        self.assertNotIn("sum_of_all_physical_layer_thicknesses_mm", document)

    def test_report_records_deleted_document_without_claiming_it_is_active(self):
        text = "\n".join([dochead(), record("DELETE_DOC", "delete-a", {"isDelete": True})])
        self.assertTrue(HELPER.inspect(self.archive(text))["documents"][0]["deleted"])

    def test_comparison_detects_geometry_and_model_changes(self):
        before = HELPER.inspect(self.archive())
        changed_geometry = board_text().replace('"width": 0.2', '"width": 0.4')
        after = HELPER.inspect(self.archive(changed_geometry, "after.epro2"))
        self.assertEqual(HELPER.compare(before, before)["changed_documents"], [])
        self.assertEqual(HELPER.compare(before, after)["changed_documents"][0]["changed_record_types"], ["LINE"])
        changed_model = board_text().replace('"synthetic-model"', '"synthetic-model-updated"')
        model_report = HELPER.inspect(self.archive(changed_model, "model.epro2"))
        self.assertEqual(HELPER.compare(before, model_report)["changed_documents"][0]["changed_record_types"], ["COMPONENT"])

    def test_comparison_ignores_tickets_but_rejects_schema_mismatch(self):
        before = HELPER.inspect(self.archive())
        ticket_change = board_text().replace('"ticket": 1', '"ticket": 4')
        after = HELPER.inspect(self.archive(ticket_change, "tickets.epro2"))
        self.assertNotEqual(before["source_sha256"], after["source_sha256"])
        self.assertEqual(HELPER.compare(before, after)["changed_documents"], [])
        invalid = copy.deepcopy(before)
        invalid["schema"] = 999
        with self.assertRaisesRegex(ValueError, "schema is incompatible"):
            HELPER.compare(invalid, after)

    def test_archive_must_contain_exactly_one_native_member(self):
        for count in (0, 2):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "Expected one"):
                HELPER.inspect(self.archive(members=count, name=f"members-{count}.epro2"))
        invalid = self.work / "invalid.epro2"
        invalid.write_bytes(b"not a ZIP archive")
        result = self.cli(invalid)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Inspection failed", result.stderr)

    def test_cli_writes_report_and_preserves_source_and_baseline(self):
        source = self.archive()
        baseline = self.work / "baseline.json"
        baseline.write_text(json.dumps(HELPER.inspect(source)), encoding="utf-8")
        original_source = source.read_bytes()
        original_baseline = baseline.read_bytes()
        output = self.work / "reports" / "inventory.json"
        result = self.cli(source, "--baseline", baseline, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["comparison"]["changed_documents"], [])
        self.assertEqual(source.read_bytes(), original_source)
        self.assertEqual(baseline.read_bytes(), original_baseline)

    def test_cli_uses_utf8_for_non_ascii_content_and_paths(self):
        text = board_text().replace('"U1"', '"测试器件"')
        source = self.archive(text, "合成工程.epro2")
        result = self.cli(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["source_name"], "合成工程.epro2")
        self.assertEqual(parsed["documents"][0]["components"][0]["ref"], "测试器件")
        output = self.work / "结果.json"
        result = self.cli(source, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("结果.json", result.stdout)

    def test_cli_rejects_output_over_source_or_baseline(self):
        source = self.archive()
        baseline = self.work / "baseline.json"
        baseline.write_text(json.dumps(HELPER.inspect(source)), encoding="utf-8")
        for protected in (source, baseline):
            before = protected.read_bytes()
            result = self.cli(source, "--baseline", baseline, "--output", protected)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must not overwrite", result.stderr)
            self.assertEqual(protected.read_bytes(), before)

    def test_cli_rejects_hardlink_output_alias(self):
        source = self.archive()
        alias = self.work / "alias.json"
        try:
            os.link(source, alias)
        except OSError as error:
            self.skipTest(f"Filesystem does not support this hardlink test: {error}")
        before = source.read_bytes()
        result = self.cli(source, "--output", alias)
        self.assertEqual(result.returncode, 2)
        self.assertIn("must not overwrite", result.stderr)
        self.assertEqual(source.read_bytes(), before)

    def test_report_hides_nested_paths_and_keeps_raw_hash_differences(self):
        before = HELPER.inspect(self.archive(private_board_text()))
        component = before["documents"][0]["components"][0]
        rendered = json.dumps(before, ensure_ascii=False)
        for marker in ("SyntheticPrivate", "/home/synthetic-private", "/synthetic-private/local",
                       "synthetic-private-marker", "synthetic-title-marker"):
            self.assertNotIn(marker, rendered)
        self.assertEqual(component["footprint"], "sensor.step")
        self.assertEqual(component["footprint_name"], "sensor.step")
        self.assertEqual(component["model_uuid"], "model.step")
        self.assertEqual(component["model_transform"]["nested"], ["[local-path]"] * 5)
        after = HELPER.inspect(self.archive(private_board_text("DifferentPrivate"), "changed.epro2"))
        self.assertEqual(component, after["documents"][0]["components"][0])
        changes = HELPER.compare(before, after)["changed_documents"]
        self.assertEqual(changes[0]["changed_record_types"], ["COMPONENT"])

    def test_redaction_keeps_colliding_dictionary_keys_and_nested_lists(self):
        values = {
            "Z:\\SyntheticOne\\model.step": "Z:\\SyntheticOne\\sensor.step",
            "Z:\\SyntheticTwo\\model.step": ["/home/synthetic-two/model.step"],
        }
        redacted = HELPER.public_report(values)
        self.assertEqual(len(redacted), 2)
        self.assertEqual(redacted["model.step"], "sensor.step")
        self.assertIn(["model.step"], list(redacted.values()))
        self.assertNotIn("SyntheticOne", json.dumps(redacted))
        self.assertNotIn("SyntheticTwo", json.dumps(redacted))
        self.assertNotIn("/home/synthetic-two", json.dumps(redacted))

    def test_redaction_handles_unc_posix_and_embedded_path_boundaries(self):
        cases = {
            r"\\synthetic-host\private\model.step": "model.step",
            "//synthetic-host/private/model.step": "model.step",
            "/home/synthetic-private/model.step": "model.step",
            "model at Z:\\SyntheticPrivate\\model.step": "[local-path]",
            "模型：/root": "[local-path]",
            "[/home/synthetic-private/model.step]": "[local-path]",
            "uri=file:///synthetic-private/model.step": "[local-path]",
            "普通器件": "普通器件",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(HELPER.public_text(value), expected)

    def test_cli_success_message_and_stdout_do_not_reveal_private_paths(self):
        source = self.archive(private_board_text())
        output = self.work / "private-output" / "inventory.json"
        result = self.cli(source, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Inventory written: inventory.json")
        self.assertEqual(result.stderr, "")
        result = self.cli(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)
        for marker in (str(self.work), "SyntheticPrivate", "/home/synthetic-private"):
            self.assertNotIn(marker, result.stdout + result.stderr)

    def test_cli_redacts_display_fields_from_an_external_baseline(self):
        source = self.archive()
        baseline = HELPER.inspect(source)
        baseline["source_sha256"] = "Z:\\SyntheticPrivate\\baseline-marker"
        path = self.work / "external-baseline.json"
        path.write_text(json.dumps(baseline), encoding="utf-8")
        result = self.cli(source, "--baseline", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("SyntheticPrivate", result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["comparison"]["baseline_source_sha256"], "baseline-marker")

    def test_cli_missing_files_bad_arguments_and_output_errors_hide_paths(self):
        source = self.archive()
        cases = [
            (self.work / "private-missing" / "missing.epro2",),
            (source, "--baseline", self.work / "private-missing" / "missing.json"),
            (source, "--output", self.work),
            (source, "--thickness-unit", str(self.work / "private-invalid-unit")),
            (source, "--unknown-option", str(self.work / "private-argument")),
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Inspection failed", result.stderr)
                self.assertNotIn(str(self.work), result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_cli_malformed_exports_and_baselines_fail_without_tracebacks(self):
        source = self.archive()
        malformed = self.archive("invalid-native-data", "malformed.epro2")
        bad_attrs = self.archive("\n".join([dochead(), record("COMPONENT", "a", {
            "attrs": ["Z:\\SyntheticPrivate\\malformed"],
        })]), "bad-attrs.epro2")
        bad_zip = self.work / "bad.epro2"
        bad_zip.write_bytes(b"not a zip archive")
        bad_baseline = self.work / "bad-baseline.json"
        bad_baseline.write_text("[]", encoding="utf-8")
        for args in ((malformed,), (bad_attrs,), (bad_zip,), (source, "--baseline", bad_baseline)):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Inspection failed", result.stderr)
                for marker in (str(self.work), "SyntheticPrivate", "Traceback"):
                    self.assertNotIn(marker, result.stdout + result.stderr)

    def test_published_schema_two_baseline_remains_compatible(self):
        # Required comparison fields captured from the previously published helper.
        # Its raw LINE digest is fixed, not recalculated by the helper under test.
        legacy = {
            "schema": 2,
            "source_sha256": "legacy-export-checksum",
            "documents": [{
                "doc_type": "PCB", "uuid": "legacy-board",
                "payload_sha256_by_type": {
                    "LINE": "33ba9187ee4d96a655ee67404e1b9cb8c1c816d03f62004d2e1d0f2ce0ec3cc6",
                },
            }],
        }
        text = "\n".join([
            dochead("legacy-board"),
            record("LINE", "line-a", {"width": 0.2, "net": "GND", "layerId": 1}),
        ])
        source = self.archive(text)
        baseline = self.work / "legacy-baseline.json"
        baseline.write_text(json.dumps(legacy), encoding="utf-8")
        result = self.cli(source, "--baseline", baseline)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["schema"], 2)
        self.assertEqual(report["comparison"]["changed_documents"], [])
        self.assertEqual(report["comparison"]["baseline_source_sha256"], "legacy-export-checksum")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    options = parser.parse_args()
    WORK_ROOT = options.work_dir.resolve()
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], "-v"])
