"""evaluate.py

Field-level comparison of an extraction against ground truth, used by the
integration tests and runnable on its own for a full report:

    uv run python -m tests.evaluate [--samples tests/samples] [--out tests/reports/latest.json] [--only w2 stack]

Single-form samples go through `/v1/documents/extract`; multi-form files
(`<name>.forms.json` ground truth: stack, consolidated statement) go through
`/v1/documents/extract/bulk` and are scored form by form, including whether
each form's type and pages were found. It drives the real API (TestClient),
so the measured path is the shipped path.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SAMPLES = Path(__file__).parent / "samples"


@dataclass
class FieldResult:
    path: str
    expected: Any
    actual: Any

    @property
    def ok(self) -> bool:
        return _same(self.expected, self.actual)


def _same(expected: Any, actual: Any) -> bool:
    if expected in (None, False, [], "") and actual in (None, False, [], ""):
        return True  # unchecked box / blank value
    if isinstance(expected, float) or isinstance(actual, float):
        try:
            return abs(float(expected) - float(actual)) < 0.005
        except (TypeError, ValueError):
            return False
    if isinstance(expected, str) and isinstance(actual, str):
        return " ".join(expected.lower().split()) == " ".join(actual.lower().split())
    return expected == actual


def compare(expected: Any, actual: Any, path: str = "") -> list[FieldResult]:
    """Flatten both trees and compare leaf by leaf. Only populated truth leaves
    and spurious populated outputs are scored, so empty boxes don't inflate accuracy."""
    if isinstance(expected, dict) or isinstance(actual, dict):
        expected, actual = expected or {}, actual or {}
        out: list[FieldResult] = []
        for key in sorted(set(expected) | set(actual)):
            out += compare(expected.get(key), actual.get(key), f"{path}.{key}" if path else key)
        return out
    if isinstance(expected, list) or isinstance(actual, list):
        expected, actual = expected or [], actual or []
        out = []
        for i in range(max(len(expected), len(actual))):
            out += compare(expected[i] if i < len(expected) else None,
                           actual[i] if i < len(actual) else None, f"{path}[{i}]")
        return out
    if expected in (None, False, "") and actual in (None, False, ""):
        return []
    return [FieldResult(path, expected, actual)]


def documents(samples: Path = SAMPLES) -> list[tuple[str, Path, Path]]:
    """(form_id, document, truth) for every single-form digital PDF and scan."""
    out = []
    for truth in sorted(samples.glob("*.json")):
        if truth.name.endswith(".forms.json"):
            continue
        form_id = truth.stem
        for doc in (samples / f"{form_id}.pdf", samples / f"{form_id}_scan.png", samples / f"{form_id}_scan.pdf"):
            if doc.exists():
                out.append((form_id, doc, truth))
    return out


def multi_documents(samples: Path = SAMPLES) -> list[tuple[Path, list[dict]]]:
    """(document, expected forms) for every multi-form file and its scan."""
    out = []
    for truth in sorted(samples.glob("*.forms.json")):
        name = truth.name.removesuffix(".forms.json")
        for doc in (samples / f"{name}.pdf", samples / f"{name}_scan.pdf"):
            if doc.exists():
                out.append((doc, json.loads(truth.read_text())))
    return out


def score_forms(expected: list[dict], forms: list[dict]) -> dict:
    """Match a bulk result's forms to the expected ones, in order."""
    report = {"forms_expected": len(expected), "forms_found": len(forms), "forms": []}
    for i, want in enumerate(expected):
        got = forms[i] if i < len(forms) else None
        entry = {"document_type": want["document_type"], "pages": want["pages"],
                 "found_type": got and got["document_type"], "found_pages": got and got["pages"]}
        entry["identified"] = bool(got) and got["document_type"] == want["document_type"] and got["pages"] == want["pages"]
        if want["fields"] and got and got.get("fields"):
            results = compare(want["fields"], got["fields"])
            wrong = [f for f in results if not f.ok]
            entry["field_accuracy"] = 1 - len(wrong) / max(len(results), 1)
            entry["errors"] = [f.__dict__ for f in wrong]
        report["forms"].append(entry)
    return report


def main() -> None:
    from fastapi.testclient import TestClient

    from tests.conftest import make_test_app

    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=Path, default=SAMPLES)
    ap.add_argument("--out", type=Path, default=Path("tests/reports/latest.json"))
    ap.add_argument("--only", nargs="*", help="form ids to evaluate (default: all)")
    args = ap.parse_args()

    app, key = make_test_app(load_models=True)
    report = []
    with TestClient(app) as client:
        for form_id, doc, truth in documents(args.samples):
            if args.only and form_id not in args.only:
                continue
            with doc.open("rb") as fh:
                r = client.post("/v1/documents/extract", files={"file": (doc.name, fh)}, headers={"X-API-Key": key})
            body = r.json()
            if r.status_code != 200:
                print(f"{doc.name:24} HTTP {r.status_code} {body.get('error', {}).get('message')}")
                report.append({"document": doc.name, "status": r.status_code})
                continue
            data = body["data"]
            results = compare(json.loads(truth.read_text()), data["fields"])
            wrong = [f for f in results if not f.ok]
            acc = 1 - len(wrong) / max(len(results), 1)
            print(f"{doc.name:24} type={data['document_type']:9} ok={data['document_type'] == form_id!s:5} "
                  f"fields={len(results) - len(wrong)}/{len(results)} ({acc:.0%}) t={data['timings_ms']['total']}ms")
            for f in wrong:
                print(f"    {f.path}: expected {f.expected!r} got {f.actual!r}")
            report.append({"document": doc.name, "expected_type": form_id, "document_type": data["document_type"],
                           "confidence": data["classification"]["confidence"], "field_accuracy": acc,
                           "errors": [f.__dict__ for f in wrong], "warnings": data["validation"]["warnings"],
                           "timings_ms": data["timings_ms"]})
        for doc, expected in multi_documents(args.samples):
            if args.only and doc.stem.removesuffix("_scan") not in args.only:
                continue
            with doc.open("rb") as fh:
                r = client.post("/v1/documents/extract/bulk", files=[("files", (doc.name, fh))],
                                headers={"X-API-Key": key})
            if r.status_code != 200:
                print(f"{doc.name:24} HTTP {r.status_code}")
                report.append({"document": doc.name, "status": r.status_code})
                continue
            file = r.json()["data"]["files"][0]
            scored = score_forms(expected, file["forms"])
            print(f"{doc.name:24} forms {sum(f['identified'] for f in scored['forms'])}/{len(expected)} identified "
                  f"(found {scored['forms_found']})")
            for f in scored["forms"]:
                acc = f" fields {f['field_accuracy']:.0%}" if "field_accuracy" in f else ""
                print(f"    {f['document_type']:9} pages={f['pages']} -> {f['found_type']} {f['found_pages']}{acc}")
                for e in f.get("errors", [])[:8]:
                    print(f"        {e['path']}: expected {e['expected']!r} got {e['actual']!r}")
            report.append({"document": doc.name, **scored})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str))
    print(f"report: {args.out}")


if __name__ == "__main__":
    main()
