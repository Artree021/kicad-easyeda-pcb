"""Local validation of the sanitized export; fixtures remain outside the package."""
from pathlib import Path
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile


import tempfile

SKILL = Path(__file__).resolve().parents[1]
PACKAGE = SKILL
_temporary = tempfile.TemporaryDirectory(prefix="pcb-skill-tests-")
WORK = Path(_temporary.name)
RECORDS = WORK
SCRIPT = SKILL / "scripts" / "inspect_epro2.py"
RESULTS = []


def check(name, condition):
    RESULTS.append({"test": name, "passed": bool(condition)})
    if not condition:
        raise AssertionError(name)


def line(kind, ident, value=None, deleted=False):
    head = {"type": kind, "id": ident}
    return json.dumps(head) + "||" + ("|" if deleted else json.dumps(value) + "|")


def fixture(path, model_dir="ExamplePrivate"):
    rows = [
        line("DOCHEAD", "doc", {"docType": "PCB", "uuid": "example-board", "account": "DO_NOT_EXPORT_ACCOUNT"}),
        line("COMPONENT", "c1", {"layerId": 1, "x": 1.125, "y": 2.5, "angle": 90, "attrs": {
            "Designator": "U1", "FootprintName": "Z:\\" + model_dir + "\\models\\sensor.step",
            "3D Model": "/home/example-private/model.step", "MPN": "PartX",
            "3D Model Transform": {"nested": ["uri=file:///private/local/model.step", "model at /home/example-private/model.step", "模型：/home/example-private/model.step", "[/home/example-private/model.step]", "模型：/root"]},
        }}),
        line("LINE", "active", {"x1": 1.0, "y1": 2.0}),
        line("LINE", "active", {"x1": 2.0, "y1": 2.0}),
        line("LINE", "removed", {"x1": 4.0}),
        line("LINE", "removed", deleted=True),
        line("POUR", "region", {"net": "GND"}),
        line("POURED", "fill", {"pourFill": {"path": [1.234567890123, 2.0]}}),
        line("LAYER_PHYS", "top", {"thickness": 10.0}),
        line("LAYER_PHYS", "core", {"thickness": 5.0}),
    ]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("board.epru", "\n".join(rows))


def run(*args):
    return subprocess.run([sys.executable, "-X", "utf8", "-B", str(SCRIPT), *map(str, args)],
                          cwd=WORK, capture_output=True, text=True, encoding="utf-8")


def no_local_paths(text):
    return not any(token in text for token in [str(WORK), str(SKILL), "ExamplePrivate", "/home/example-private", "/private/local", "Z:\\"])


def main():
    ast.parse(SCRIPT.read_text(encoding="utf-8"))
    check("script_parses_without_bytecode", True)
    source = WORK / "fixture.epro2"
    fixture(source)
    before_bytes = source.read_bytes()
    first = WORK / "baseline.json"
    r = run(source, "--output", first, "--thickness-unit", "mil")
    check("successful_output_is_basename_only", r.returncode == 0 and r.stdout.strip() == "Inventory written: baseline.json" and not r.stderr)
    report = json.loads(first.read_text(encoding="utf-8"))
    doc = report["documents"][0]
    check("inventory_and_precision", doc["counts"]["LINE"] == 1 and doc["pours"]["POURED"] == 1 and doc["components"][0]["x_raw"] == 1.125)
    check("revisions_and_tombstones", doc["record_revisions_applied"] == 1 and doc["tombstones_applied"] == 1)
    check("explicit_thickness_conversion", abs(doc["sum_of_all_physical_layer_thicknesses_mm"] - 0.381) < 1e-12)
    rendered = first.read_text(encoding="utf-8")
    check("account_metadata_omitted", "DO_NOT_EXPORT_ACCOUNT" not in rendered)
    check("nested_paths_redacted", no_local_paths(rendered) and doc["components"][0]["footprint"] == "sensor.step" and doc["components"][0]["model_uuid"] == "model.step")
    check("unicode_and_bracket_path_boundaries_redacted", all(item == "[local-path]" for item in doc["components"][0]["model_transform"]["nested"]))
    r = run(source)
    check("stdout_inventory_has_no_absolute_paths", r.returncode == 0 and no_local_paths(r.stdout + r.stderr))
    check("unknown_units_do_not_convert", "thickness_mm" not in json.loads(r.stdout)["documents"][0]["physical_layers"][0])
    r = run(source, "--baseline", first)
    check("identical_baseline_has_no_changes", r.returncode == 0 and json.loads(r.stdout)["comparison"]["changed_documents"] == [])
    changed = WORK / "changed.epro2"
    fixture(changed, "DifferentPrivate")
    r = run(changed, "--baseline", first)
    changes = json.loads(r.stdout)["comparison"]["changed_documents"]
    check("raw_hash_detects_same_basename_different_source_path", r.returncode == 0 and changes[0]["changed_record_types"] == ["COMPONENT"])
    r = run(source, "--output", source)
    check("source_overwrite_rejected_without_changes", r.returncode == 2 and source.read_bytes() == before_bytes and no_local_paths(r.stderr))
    baseline_bytes = first.read_bytes()
    r = run(source, "--baseline", first, "--output", first)
    check("baseline_overwrite_rejected_without_changes", r.returncode == 2 and first.read_bytes() == baseline_bytes)
    for name, args in [
        ("missing_source", (WORK / "private-missing" / "missing.epro2",)),
        ("missing_baseline", (source, "--baseline", WORK / "private-missing" / "missing.json")),
        ("output_is_directory", (source, "--output", WORK)),
        ("invalid_argument", (source, "--thickness-unit", str(WORK / "invalid-unit"))),
    ]:
        r = run(*args)
        check(name + "_fails_without_absolute_paths", r.returncode == 2 and no_local_paths(r.stdout + r.stderr) and "Traceback" not in r.stderr)
    bad_zip = WORK / "bad.epro2"
    bad_zip.write_bytes(b"not a zip")
    r = run(bad_zip)
    check("invalid_zip_safe_error", r.returncode == 2 and no_local_paths(r.stderr) and "Traceback" not in r.stderr)
    two = WORK / "multiple.epro2"
    with zipfile.ZipFile(two, "w") as archive:
        archive.writestr("one.epru", "")
        archive.writestr("two.epru", "")
    r = run(two)
    check("multiple_epru_rejected", r.returncode == 2 and no_local_paths(r.stderr))
    malformed = WORK / "malformed.epro2"
    with zipfile.ZipFile(malformed, "w") as archive:
        archive.writestr("board.epru", "not valid native records")
    r = run(malformed)
    check("malformed_native_safe_error", r.returncode == 2 and no_local_paths(r.stderr) and "Traceback" not in r.stderr)
    links = []
    for f in PACKAGE.rglob("*.md"):
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", f.read_text(encoding="utf-8")):
            if "://" not in target and not target.startswith("#"):
                resolved = (f.parent / target.split("#")[0]).resolve()
                links.append({"source": f.relative_to(PACKAGE).as_posix(), "target": target,
                              "exists": resolved.is_file(), "inside_package": resolved.is_relative_to(PACKAGE.resolve())})
    check("all_document_links_resolve_inside_package", bool(links) and all(x["exists"] and x["inside_package"] for x in links))
    check("public_skill_has_no_binary_assets", all(f.suffix in {".md", ".yaml", ".py"} for folder in (SKILL / "scripts", SKILL / "references", SKILL / "agents") for f in folder.rglob("*") if f.is_file()))
    (RECORDS / "skill-export-validation.json").write_text(json.dumps({"status": "passed", "tests": RESULTS, "links": links, "scope": "Synthetic offline fixtures; no real EDA application, account, manufacturing or board operation."}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Passed {len(RESULTS)} checks and {len(links)} local links.")


if __name__ == "__main__":
    main()
