#!/usr/bin/env python3
"""
secrets_check.py — Scan for exposed API keys and credentials.

Checks staged files (pre-commit) or the full repo for patterns that look
like real credentials. Blocks git commits if anything is found.

Usage:
  python secrets_check.py               Scan all files in current directory
  python secrets_check.py --staged-only Scan only git-staged files (used by pre-commit hook)
  python secrets_check.py --explain     Show what each pattern detects
  python secrets_check.py --help        Show this message

Exit codes:
  0 — clean (no secrets found)
  1 — secrets found (commit blocked)
"""

from __future__ import annotations

import re
import sys
import os
import subprocess
from pathlib import Path

# ─── Patterns ─────────────────────────────────────────────────────────────────

PATTERNS = [
    {
        "name": "Meta / Facebook access token",
        "regex": re.compile(r"EAAn[A-Za-z0-9]{50,}"),
        "description": "Matches Meta/Facebook Graph API access tokens (start with EAAn).",
    },
    {
        "name": "Google OAuth access token",
        "regex": re.compile(r"ya29\.[A-Za-z0-9\-_]{40,}"),
        "description": "Matches short-lived Google OAuth access tokens (start with ya29.).",
    },
    {
        "name": "Google refresh token",
        "regex": re.compile(r"1//[A-Za-z0-9\-_]{40,}"),
        "description": "Matches Google OAuth refresh tokens (start with 1//).",
    },
    {
        "name": "Hardcoded credential assignment",
        "regex": re.compile(
            r"""(?:TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL|ACCESS_TOKEN)\s*=\s*['"][A-Za-z0-9\-_/\+\.]{16,}['"]""",
            re.IGNORECASE,
        ),
        "description": "Matches variable assignments like MY_API_KEY = 'abc123...' in source files.",
    },
    {
        "name": "Long bearer token in source",
        "regex": re.compile(r"""[Bb]earer\s+[A-Za-z0-9\-_\.]{30,}"""),
        "description": "Matches Bearer tokens hardcoded in source files.",
    },
]

# ─── Skip lists ───────────────────────────────────────────────────────────────

SKIP_FILES = {
    ".env.example",
    "secrets_check.py",  # this file contains patterns, not secrets
    "README.md",
    "SKILL.md",
}

SKIP_DIRS = {
    "venv", ".venv", "env", "__pycache__", ".git",
    "node_modules", "dist", "build", "docs",
}

SCAN_EXTENSIONS = {
    ".py", ".js", ".ts", ".json", ".yaml", ".yml",
    ".sh", ".env", ".cfg", ".ini", ".toml",
}


# ─── File collection ──────────────────────────────────────────────────────────

def get_staged_files() -> list[Path]:
    """Return list of staged file paths."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, check=True
        )
        paths = []
        for line in result.stdout.strip().splitlines():
            p = Path(line)
            if p.suffix in SCAN_EXTENSIONS and p.name not in SKIP_FILES:
                paths.append(p)
        return paths
    except subprocess.CalledProcessError:
        return []


def get_all_files(root: Path) -> list[Path]:
    """Return all scannable files in the repo, excluding skip dirs."""
    paths = []
    for p in root.rglob("*"):
        if p.is_file():
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if p.name in SKIP_FILES:
                continue
            if p.suffix in SCAN_EXTENSIONS:
                paths.append(p)
    return paths


# ─── Scanner ──────────────────────────────────────────────────────────────────

def scan_file(path: Path) -> list[dict]:
    """Scan a single file for secret patterns. Returns list of findings."""
    findings = []
    try:
        content = path.read_text(errors="ignore")
        lines = content.splitlines()
        for lineno, line in enumerate(lines, start=1):
            for pattern in PATTERNS:
                if pattern["regex"].search(line):
                    # Mask the matched value in output
                    masked_line = pattern["regex"].sub("[REDACTED]", line).strip()
                    findings.append({
                        "file": str(path),
                        "line": lineno,
                        "pattern": pattern["name"],
                        "content": masked_line[:120],
                    })
    except (OSError, PermissionError):
        pass
    return findings


def scan(files: list[Path]) -> list[dict]:
    """Scan a list of files. Returns all findings."""
    all_findings = []
    for f in files:
        all_findings.extend(scan_file(f))
    return all_findings


# ─── Output ───────────────────────────────────────────────────────────────────

def print_clean():
    print()
    print("  ✓  No API keys or credentials found.")
    print()


def print_blocked(findings: list[dict]):
    print()
    print("━" * 49)
    print()
    print("  COMMIT BLOCKED — CREDENTIAL DETECTED")
    print()
    print("━" * 49)
    print()
    print(f"  Found {len(findings)} potential credential(s) in")
    print(f"  file(s) you're about to commit.")
    print()

    for f in findings:
        print(f"  File:    {f['file']} (line {f['line']})")
        print(f"  Pattern: {f['pattern']}")
        print(f"  Content: {f['content']}")
        print()

    print("─" * 49)
    print()
    print("  Exposing credentials on GitHub means anyone")
    print("  can access and bill to your ad accounts.")
    print()
    print("  To fix:")
    print("  ① Remove the value from the file")
    print("  ② Store it in .env instead (.env is")
    print("     gitignored and safe)")
    print("  ③ Re-run your commit")
    print()
    print("  Your .env file is already protected.")
    print("  The /ads-connect skill writes credentials")
    print("  there automatically — no manual editing needed.")
    print()
    print("  Questions? Run: python secrets_check.py --explain")
    print()


def print_explain():
    print()
    print("  SECRETS_CHECK — WHAT EACH PATTERN DETECTS")
    print()
    for p in PATTERNS:
        print(f"  {p['name']}")
        print(f"    {p['description']}")
        print()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    if "--explain" in args:
        print_explain()
        sys.exit(0)

    staged_only = "--staged-only" in args

    if staged_only:
        files = get_staged_files()
        if not files:
            # No staged files to scan — not a problem
            sys.exit(0)
    else:
        root = Path(__file__).parent
        files = get_all_files(root)

    findings = scan(files)

    if findings:
        print_blocked(findings)
        sys.exit(1)
    else:
        if not staged_only:
            print_clean()
        sys.exit(0)


if __name__ == "__main__":
    main()
