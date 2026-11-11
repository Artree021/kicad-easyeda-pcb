"""Read-only inventory of EasyEDA .epro2 exports; no correctness sign-off.

Observed format: one .epru ZIP member with JSON-header || JSON-payload | lines.
Only explicit deletion tombstones and later revisions of the same type/id are
resolved. Payloads are not simplified, rounded, or stripped of 3D properties.
Account metadata in DOCHEAD is deliberately omitted from the report.
Displayed local paths are reduced to basenames; raw payload hashes are preserved.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
import zipfile
import zlib


SCHEMA = 2


def public_text(value):
    """Remove local absolute paths from display text without touching hashes."""
    stripped = value.strip()
    if re.match(r"^[A-Za-z]:[\\/]", stripped) or stripped.startswith("\\\\"):
        return PureWindowsPath(stripped).name or "[local-path]"
    if stripped.startswith("/") and not stripped.startswith("//"):
        return PurePosixPath(stripped).name or "[local-path]"
    if stripped.startswith("//"):
        return PurePosixPath(stripped).name or "[local-path]"
    if re.search(r"[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/]|file://", value, re.I):
        return "[local-path]"
    if re.search(r"/[^/\s\"'<>\[\](),;]+/[^\s\"'<>\[\](),;]+", value):
        return "[local-path]"
    if re.search(r"(?:^|[\s=:：\"'(\[{])/(?!/)[^\s\"'<>]+", value):
        return "[local-path]"
    return value


def public_report(value):
    if isinstance(value, str):
        return public_text(value)
    if isinstance(value, list):
        return [public_report(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            safe_key = public_text(key) if isinstance(key, str) else key
            if safe_key in result:
                safe_key = f"{safe_key}~{digest(key)[:12]}"
            result[safe_key] = public_report(item)
        return result
    return value


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Inspection failed: invalid arguments; use --help.\n")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def parse_native(text):
    decoder = json.JSONDecoder()
    documents = []
    current = None
    seen_documents = set()
    for line_number, original in enumerate(text.splitlines(), 1):
        line = original.strip()
        if not line:
            continue
        try:
            header, end = decoder.raw_decode(line)
            if not isinstance(header, dict) or not isinstance(header.get("type"), str):
                raise ValueError("missing record type")
            if line[end:end + 2] != "||":
                raise ValueError("expected || after record header")
            payload = line[end + 2:]
            tombstone = payload in ("", "|")
            if not tombstone:
                value, value_end = decoder.raw_decode(payload)
                if payload[value_end:].strip() not in ("", "|"):
                    raise ValueError("unexpected data after record payload")
            if header["type"] == "DOCHEAD":
                if tombstone or not isinstance(value, dict):
                    raise ValueError("invalid document header")
                identity = (value.get("docType"), value.get("uuid"))
                if not all(isinstance(x, str) and x for x in identity):
                    raise ValueError("document type/UUID missing")
                if identity in seen_documents:
                    raise ValueError("duplicate document identity; unsupported export history")
                seen_documents.add(identity)
                current = {"doc_type": identity[0], "uuid": identity[1],
                           "records": {}, "tombstones": 0, "revisions": 0}
                documents.append(current)
                continue
            if current is None:
                raise ValueError("record precedes first DOCHEAD")
            identity = (header["type"], canonical(header.get("id", ["anonymous", line_number])))
            if tombstone:
                if "id" not in header:
                    raise ValueError("tombstone lacks record id")
                current["tombstones"] += 1
                current["records"].pop(identity, None)
            else:
                if not isinstance(value, dict):
                    raise ValueError("non-object payload is unsupported")
                if identity in current["records"]:
                    current["revisions"] += 1
                current["records"][identity] = (header, value)
        except (ValueError, TypeError) as error:
            raise ValueError(f"Line {line_number}: {error}") from error
    if not documents:
        raise ValueError("No documents found")
    return documents


def inventory_document(document, thickness_unit):
    records = list(document["records"].values())
    counts = Counter(h["type"] for h, _ in records)
    groups = defaultdict(list)
    for header, value in records:
        groups[header["type"]].append({"id": header.get("id"), "value": value})
    result = {
        "doc_type": document["doc_type"], "uuid": document["uuid"],
        "deleted": any(h["type"] == "DELETE_DOC" and v.get("isDelete") for h, v in records),
        "counts": dict(sorted(counts.items())),
        "payload_sha256_by_type": {kind: digest(sorted(items, key=canonical))
                                   for kind, items in sorted(groups.items())},
        "tombstones_applied": document["tombstones"],
        "record_revisions_applied": document["revisions"],
    }
    if document["doc_type"] != "PCB":
        return result
    attrs = defaultdict(dict)
    for header, value in records:
        if header["type"] == "ATTR" and "parentId" in value and "key" in value:
            attrs[value["parentId"]][value["key"]] = value.get("value")
    components = []
    layers = []
    for header, value in records:
        if header["type"] == "COMPONENT":
            properties = {**value.get("attrs", {}), **attrs.get(header.get("id"), {})}
            components.append({
                "id": header.get("id"), "ref": properties.get("Designator"),
                "footprint": properties.get("FootprintName", properties.get("Footprint")),
                "lcsc": properties.get("LCSC"), "mpn": properties.get("MPN"),
                "layer_id": value.get("layerId"), "x_raw": value.get("x"),
                "y_raw": value.get("y"), "angle_deg": value.get("angle"),
                "model_uuid": properties.get("3D Model"),
                "model_transform": properties.get("3D Model Transform"),
            })
        elif header["type"] == "LAYER_PHYS":
            layer = {"record_id": header.get("id"), "thickness_raw": value.get("thickness")}
            raw = layer["thickness_raw"]
            if thickness_unit != "unknown" and isinstance(raw, (int, float)) and not isinstance(raw, bool):
                layer["thickness_mm"] = raw * (0.0254 if thickness_unit == "mil" else 1)
            layers.append(layer)
    components.sort(key=lambda item: (item["ref"] or "", str(item["id"])))
    result.update({
        "components": components,
        "models_bound": sum(bool(component["model_uuid"]) for component in components),
        "components_without_model": [item["ref"] or item["id"] for item in components if not item["model_uuid"]],
        "pours": {kind: counts[kind] for kind in ("POUR", "POURED", "FILL")},
        "physical_layers": layers,
        "thickness_unit_asserted_by_caller": thickness_unit,
    })
    if layers and all("thickness_mm" in item for item in layers):
        result["sum_of_all_physical_layer_thicknesses_mm"] = sum(item["thickness_mm"] for item in layers)
    return result


def inspect(path, thickness_unit="unknown"):
    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if item.filename.lower().endswith(".epru")]
        if len(members) != 1:
            raise ValueError(f"Expected one .epru member, found {len(members)}")
        if members[0].file_size > 256 * 1024 * 1024:
            raise ValueError("Export exceeds this helper's 256 MiB inspection limit")
        text = archive.read(members[0]).decode("utf-8-sig")
    return public_report({
        "schema": SCHEMA, "status": "INVENTORY_ONLY_NOT_ELECTRICAL_OR_MANUFACTURING_SIGNOFF",
        "source_name": path.name, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "documents": [inventory_document(document, thickness_unit) for document in parse_native(text)],
        "scope": "Exact active payload inventory. Record ticket/header metadata are excluded; geometry, model attributes and polygon vertices are not normalized. No pin-net equivalence, fill validity, DRC, model alignment or SMT placement is established.",
        "privacy": "Local absolute paths in displayed strings are reduced to basenames or redaction markers. Payload hashes use the original data. Project identifiers and part numbers remain; review before sharing.",
    })


def compare(before, after):
    if before.get("schema") != SCHEMA:
        raise ValueError("Baseline schema is incompatible")
    previous = {(d["doc_type"], d["uuid"]): d for d in before["documents"]}
    current = {(d["doc_type"], d["uuid"]): d for d in after["documents"]}
    changes = []
    for key in sorted(previous.keys() | current.keys()):
        if key not in previous or key not in current:
            changes.append({"doc_type": key[0], "uuid": key[1], "change": "added" if key in current else "removed"})
            continue
        a = previous[key]["payload_sha256_by_type"]
        b = current[key]["payload_sha256_by_type"]
        changed = [kind for kind in sorted(a.keys() | b.keys()) if a.get(kind) != b.get(kind)]
        if changed:
            changes.append({"doc_type": key[0], "uuid": key[1], "changed_record_types": changed})
    return {"baseline_source_sha256": before["source_sha256"], "changed_documents": changes,
            "interpretation": "Differences require inspection; no difference is waived automatically."}


def main():
    parser = PrivateArgumentParser(prog="inspect_epro2.py", description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, help="JSON report path; default writes JSON to stdout")
    parser.add_argument("--baseline", type=Path, help="Earlier report from the same native project")
    parser.add_argument("--thickness-unit", choices=("unknown", "mil", "mm"), default="unknown")
    args = parser.parse_args()
    try:
        if args.output and args.output.resolve() in {args.source.resolve(), args.baseline.resolve() if args.baseline else None}:
            raise ValueError("Output must not overwrite source or baseline")
        report = inspect(args.source, args.thickness_unit)
        if args.baseline:
            report["comparison"] = compare(json.loads(args.baseline.read_text(encoding="utf-8-sig")), report)
        rendered = json.dumps(public_report(report), ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
            print(f"Inventory written: {public_text(args.output.name)}")
        else:
            print(rendered, end="")
        return 0
    except OSError as error:
        print(f"Inspection failed: file operation failed ({type(error).__name__}, errno={error.errno}).", file=sys.stderr)
        return 2
    except (ValueError, KeyError, TypeError, AttributeError, RuntimeError, EOFError, zipfile.BadZipFile, zlib.error) as error:
        print(f"Inspection failed: invalid or unsupported export/report ({type(error).__name__}).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
