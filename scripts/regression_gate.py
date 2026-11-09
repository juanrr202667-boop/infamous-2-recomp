"""Compare native and Python fixture outcomes against an approved baseline.

This is an outcome gate: it detects a test that passed before and now fails,
errors, is skipped, or disappeared. It does not claim pixel, audio, or gameplay
coverage that the fixtures do not have. The baseline also records the patch
manifest hash so a changed SDK series cannot silently inherit an old approval.

Baselines are updated only with --update-baseline, never automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "tests" / "baselines" / "regression-gate.json"
DEFAULT_BUILD = ROOT / "build" / "windows-tests"
DEFAULT_REPORT = ROOT / "build" / "regression"
PATCH_MANIFEST = ROOT / "scripts" / "patches" / "manifest.json"


def patch_stack_hash() -> str:
    manifest = json.loads(PATCH_MANIFEST.read_text(encoding="utf-8"))
    entries = manifest.get("patches", [])
    material = "\n".join(f"{entry['order']}:{entry['name']}:{entry['sha256']}" for entry in entries)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _case_status(case: ET.Element) -> str:
    if case.find("failure") is not None or case.find("error") is not None:
        return "failed"
    if case.find("skipped") is not None:
        return "skipped"
    return "passed"


def parse_junit(path: Path, prefix: str = "") -> dict[str, str]:
    tree = ET.parse(path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    results: dict[str, str] = {}
    for suite in suites:
        for case in suite.findall("testcase"):
            classname = case.get("classname", "")
            name = case.get("name", "")
            key = name if not classname or prefix == "ctest" else f"{classname}::{name}"
            if prefix == "pytest" and not classname:
                key = name
            if key in results:
                key = f"{key}#{len(results)}"
            results[key] = _case_status(case)
    return results


def run_ctest(build_dir: Path, report_dir: Path, include_gpu: bool) -> tuple[dict[str, str], str]:
    junit = report_dir / "ctest.xml"
    if junit.exists():
        junit.unlink()
    command = [
        "ctest", "--test-dir", str(build_dir),
        "--output-junit", str(junit),
        "--output-on-failure", "-j", "1",
    ]
    command += ["-LE", "game-local" if include_gpu else "gpu-optional|game-local"]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    (report_dir / "ctest.log").write_text(
        completed.stdout + ("\n[stderr]\n" + completed.stderr if completed.stderr else ""),
        encoding="utf-8", errors="replace")
    if not junit.is_file():
        raise SystemExit(
            "CTest produced no JUnit report. Configure the tests with "
            "scripts/build.ps1 -Target tests and run from the repository root.\n"
            + completed.stdout[-2000:])
    return parse_junit(junit, "ctest"), completed.stdout


def run_pytest(report_dir: Path) -> tuple[dict[str, str], str]:
    junit = report_dir / "pytest.xml"
    if junit.exists():
        junit.unlink()
    python = sys.executable
    command = [python, "-m", "pytest", "tests", "-q", "--junitxml", str(junit)]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    (report_dir / "pytest.log").write_text(
        completed.stdout + ("\n[stderr]\n" + completed.stderr if completed.stderr else ""),
        encoding="utf-8", errors="replace")
    if not junit.is_file():
        raise SystemExit("pytest produced no JUnit report:\n" + completed.stdout[-2000:])
    return parse_junit(junit, "pytest"), completed.stdout


def compare(expected: dict[str, str], actual: dict[str, str]) -> dict:
    failed = []
    skipped_regressions = []
    missing = []
    new = []
    new_skips = []
    improved = []
    for name, want in sorted(expected.items()):
        got = actual.get(name)
        if got is None:
            missing.append(name)
        elif got == "failed":
            failed.append(name)
        elif want == "passed" and got == "skipped":
            skipped_regressions.append(name)
        elif want == "skipped" and got == "passed":
            improved.append(name)
    for name, got in sorted(actual.items()):
        if name not in expected:
            new.append(name)
            if got == "skipped":
                new_skips.append(name)
    return {
        "failed": failed,
        "skipped_regressions": skipped_regressions,
        "missing": missing,
        "new": new,
        "new_skips": new_skips,
        "improved": improved,
        "ok": not (failed or skipped_regressions or missing),
    }


def counts(results: dict[str, str]) -> dict[str, int]:
    return {
        "total": len(results),
        "passed": sum(1 for value in results.values() if value == "passed"),
        "failed": sum(1 for value in results.values() if value == "failed"),
        "skipped": sum(1 for value in results.values() if value == "skipped"),
    }


def sanitize(text: str) -> str:
    return text.replace(str(ROOT), "<repo>").replace(str(ROOT).replace("\\", "/"), "<repo>")


def write_report(path: Path, payload: dict) -> None:
    lines = [
        "# Regression gate report",
        "",
        f"- Result: **{'PASS' if payload['ok'] else 'FAIL'}**",
        f"- Generated: {payload['generated_at']}",
        f"- Patch stack: {payload['patch_count']} patches, manifest `{payload['patch_hash'][:16]}...`",
        f"- Environment: {payload['environment']}",
        "",
        "| Suite | Passed | Failed | Skipped | Missing | New |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    available = [suite for suite in ("ctest", "pytest") if payload.get(suite)]
    for suite in available:
        data = payload[suite]
        lines.append(
            f"| {suite} | {data['counts']['passed']} | {data['counts']['failed']} | "
            f"{data['counts']['skipped']} | {len(data['comparison']['missing'])} | "
            f"{len(data['comparison']['new'])} |")
    lines += ["", "## Differences", ""]
    any_difference = False
    for suite in available:
        comparison = payload[suite]["comparison"]
        for label, key in (("Failed", "failed"), ("Skipped regression", "skipped_regressions"),
                           ("Missing", "missing"), ("New", "new"),
                           ("New skip", "new_skips"), ("Now passing", "improved")):
            values = comparison[key]
            if values:
                any_difference = True
                lines.append(f"- **{suite} / {label}:** " + ", ".join(f"`{value}`" for value in values))
    if not any_difference:
        lines.append("- None.")
    lines += [
        "",
        "A skip or an excluded test is not a pass. This gate compares fixture outcomes; "
        "it does not establish game execution, visual fidelity, or gameplay.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--ctest", action="store_true", help="run native CTest fixtures")
    parser.add_argument("--pytest", action="store_true", help="run Python tests")
    parser.add_argument("--include-gpu", action="store_true", help="include gpu-optional CTest labels")
    parser.add_argument("--update-baseline", action="store_true",
                        help="approve current outcomes; refused if anything failed")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.ctest and not args.pytest:
        args.ctest = args.pytest = True
    report_dir = args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    current: dict[str, dict[str, str]] = {}
    logs: dict[str, str] = {}
    if args.ctest:
        if not (args.build_dir / "CTestTestfile.cmake").is_file():
            raise SystemExit(f"No configured CTest build at {args.build_dir}")
        current["ctest"], logs["ctest"] = run_ctest(args.build_dir, report_dir, args.include_gpu)
    if args.pytest:
        current["pytest"], logs["pytest"] = run_pytest(report_dir)

    manifest = json.loads(PATCH_MANIFEST.read_text(encoding="utf-8"))
    payload = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": f"{platform.system()} {platform.release()}, Python {platform.python_version()}",
        "patch_count": manifest["patch_count"],
        "patch_hash": patch_stack_hash(),
        "ok": True,
        "ctest": None,
        "pytest": None,
    }
    for suite, results in current.items():
        payload[suite] = {"counts": counts(results), "results": results}

    if args.update_baseline:
        failures = {suite: counts(results)["failed"] for suite, results in current.items()}
        if any(failures.values()):
            raise SystemExit(f"Refusing to approve a baseline with failures: {failures}")
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline = {
            "schema": 1,
            "approved_at": payload["generated_at"],
            "patch_count": payload["patch_count"],
            "patch_hash": payload["patch_hash"],
            "environment": payload["environment"],
            "ctest": current.get("ctest", {}),
            "pytest": current.get("pytest", {}),
        }
        args.baseline.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        print(f"Baseline updated: {args.baseline}")
        return 0

    if not args.baseline.is_file():
        raise SystemExit(f"Baseline not found: {args.baseline}. Run with --update-baseline on a green tree.")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    if baseline.get("patch_hash") and baseline["patch_hash"] != payload["patch_hash"]:
        payload["ok"] = False
        payload["patch_mismatch"] = True
    for suite, results in current.items():
        expected = baseline.get(suite, {})
        comparison = compare(expected, results)
        payload[suite]["comparison"] = comparison
        if not comparison["ok"]:
            payload["ok"] = False
    payload["report"] = str(report_dir / "report.md")
    write_report(report_dir / "report.md", payload)
    (report_dir / "report.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                                             encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for suite in current:
            data = payload[suite]
            print(f"{suite}: {data['counts']['passed']} passed, {data['counts']['failed']} failed, "
                  f"{data['counts']['skipped']} skipped")
            for key in ("failed", "skipped_regressions", "missing", "new", "new_skips"):
                values = data["comparison"][key]
                if values:
                    print(f"  {key}: {len(values)}")
        if payload.get("patch_mismatch"):
            print("Patch stack hash differs from the approved baseline.")
        print(f"Report: {report_dir / 'report.md'}")
        print("RESULT:", "PASS" if payload["ok"] else "FAIL")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
