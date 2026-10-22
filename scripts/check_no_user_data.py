#!/usr/bin/env python
"""Scan tracked files for personal data that must not ship in the repo.

A plugin repo gets cloned, copied, and redistributed. If it carries one
person's CV, name, email, or local paths, that leaks to every installer.
This is the automated form of the "sweep for leaked user data" step.

Usage:
    python scripts/check_no_user_data.py            # scan the working tree
    python scripts/check_no_user_data.py --staged   # scan staged content

Exit code 0 = clean, 1 = findings.

The rules are deliberately conservative: they flag things that are almost
always wrong in a plugin repo. A false positive is a one-line allowlist
entry, not a leak.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- Patterns -------------------------------------------------------------
# Each is (name, compiled regex, explanation).
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "windows-local-path",
        # Both separators: Windows paths appear as both C:\Users\... and
        # C:/Users/... in configs, docs, and error messages.
        re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\s\"'<>|]{2,}", re.IGNORECASE),
        "an absolute Windows user path (a machine-specific path leaked into "
        "the source)",
    ),
    (
        "posix-home-path",
        re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]{1,20}/"),
        "an absolute home directory path",
    ),
    (
        "scratch-dir",
        re.compile(r"/(?:d|c|Users)/[A-Za-z0-9_.-]*/AI_Scratch", re.IGNORECASE),
        "a local scratch workspace path",
    ),
    (
        "email-address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "an email address (the plugin must not carry a real contact)",
    ),
    (
        "phone-number",
        re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)"),
        "a phone number",
    ),
]

# The reserved domains RFC 2606 defines for documentation and testing. An
# address at one of these is a placeholder, not a person's contact, and
# flagging it would make the check cry wolf.
RESERVED_EMAIL_DOMAINS = {
    "example.com", "example.net", "example.org",
    "example", "test", "invalid", "localhost",
    "domain.com", "email.com", "mail.com",
}


def _is_reserved_email(address: str) -> bool:
    """True when an address is at a reserved documentation domain."""
    domain = address.rsplit("@", 1)[-1].lower()
    if domain in RESERVED_EMAIL_DOMAINS:
        return True
    # A subdomain of a reserved domain is reserved too.
    return any(domain.endswith("." + d) for d in RESERVED_EMAIL_DOMAINS)

# --- Files exempt from the scan -------------------------------------------
# The binary-ish assets and the vendored third-party sources carry their own
# text (font names, class comments, licence text) that trips the patterns
# without being personal data.
EXEMPT_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ttf", ".otf", ".cls", ".pdf"}
EXEMPT_DIRS = {".git"}
EXEMPT_FILES = {
    "LICENSE",
    "templates/LICENSE-FONTS.md",
    "templates/yaac/LICENSE.md",
    "templates/altacv/LICENSE.md",
}

# Paths that legitimately contain an address or a path: the licence headers
# and the author attribution. These are explicit, not blanket exemptions.
ALLOWLIST: dict[str, list[str]] = {
    "LICENSE": ["Bryan Pawlowski"],
    "plugin.yaml": ["Bryan Pawlowski"],
    "skills/evergreen-career/SKILL.md": ["Bryan Pawlowski"],
    "skills/evergreen-cvcraft/SKILL.md": ["Bryan Pawlowski"],
}

# Files that are data, not source: a template's fonts and class files are
# exempt by suffix; these are the text assets that are legitimately local.
TEXT_ASSET_DIRS = {"templates"}

# The checker contains the patterns it looks for, so it will always match
# itself. It exempts itself rather than contorting the patterns.
SELF = "scripts/check_no_user_data.py"


def tracked_files(staged: bool) -> list[Path]:
    """The files to scan."""
    if staged:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=REPO_ROOT, capture_output=True, text=True, errors="replace",
        )
        names = [n for n in out.stdout.splitlines() if n.strip()]
    else:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT, capture_output=True, text=True, errors="replace",
        )
        names = [n for n in out.stdout.splitlines() if n.strip()]

    paths = []
    for name in names:
        p = REPO_ROOT / name
        if not p.is_file():
            continue
        parts = set(p.parts)
        if parts & EXEMPT_DIRS:
            continue
        if p.name in EXEMPT_FILES:
            continue
        if p.relative_to(REPO_ROOT).as_posix() == SELF:
            continue
        if p.suffix.lower() in EXEMPT_SUFFIXES:
            continue
        # A vendored third-party template is not ours to police; its licence
        # and upstream text are tracked separately.
        if any(part in TEXT_ASSET_DIRS for part in p.parts) and \
                p.suffix.lower() in {".cls", ".md", ".yaml"} and \
                p.name not in {"README.md", "template.yaml"}:
            continue
        paths.append(p)
    return paths


def scan_text(rel: str, text: str) -> list[str]:
    """Findings in one file."""
    allow = ALLOWLIST.get(rel, [])
    findings = []
    for name, pattern, why in PATTERNS:
        for match in pattern.finditer(text):
            snippet = match.group(0)
            if any(a in snippet for a in allow):
                continue
            if name == "email-address" and _is_reserved_email(snippet):
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            findings.append(
                f"{rel}:{line_no}  [{name}] {why}\n"
                f"    {snippet[:100]!r}"
            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true",
                        help="scan staged content instead of tracked files")
    args = parser.parse_args()

    files = tracked_files(args.staged)
    if not files:
        print("No files to scan.")
        return 0

    all_findings: list[str] = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        all_findings.extend(scan_text(rel, text))

    if all_findings:
        print(f"Personal data found in {len(all_findings)} place(s):\n")
        for finding in all_findings:
            print("  " + finding)
        print("\nA plugin repo must not carry a real person's data.")
        print("Remove it, or add an explicit ALLOWLIST entry in this script.")
        return 1

    print(f"Clean: scanned {len(files)} tracked file(s), no personal data found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
