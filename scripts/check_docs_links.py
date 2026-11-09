"""Check relative Markdown links in the public documentation."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


def markdown_files(root: Path) -> list[Path]:
    files = []
    for pattern in ("*.md", "docs/*.md", ".github/*.md", ".github/**/*.md"):
        files.extend(root.glob(pattern))
    return sorted({path.resolve() for path in files if path.is_file()})


def check(files: list[Path]) -> list[dict]:
    problems = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in LINK.finditer(text):
            target = match.group(1).strip()
            if target.startswith(SKIP_PREFIXES):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                line = text.count("\n", 0, match.start()) + 1
                problems.append({"file": path, "line": line, "target": target})
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    files = markdown_files(args.root)
    problems = check(files)
    if problems:
        print(f"Broken relative links: {len(problems)}")
        for problem in problems:
            print(f"- {problem['file'].relative_to(args.root)}:{problem['line']}: {problem['target']}")
        return 1
    print(f"Documentation links: {len(files)} file(s), no broken relative links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
