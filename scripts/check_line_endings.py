#!/usr/bin/env python
"""Guard against Windows CRLF line endings sneaking into tracked text files.

The repo declares ``eol=lf`` in ``.gitattributes`` so that git normalizes
checkouts, but that only works if a file is *normalized when it is first
committed*. A file that enters the index already carrying CRLF keeps it (git
stores what you give it; ``eol=lf`` affects checkout, not what is stored),
and every later clone on Linux or macOS gets mixed or CRLF line endings,
which break ``git diff`` noise, LaTeX's line-oriented macros, and the
selftest's byte-comparisons. This catches it at the boundary.

It scans the text files and flags any that contain a carriage return. The
binary-ish assets (fonts, images, PDFs, LaTeX class files) and vendored
third-party template sources are exempt, for the same reasons the personal-
data check exempts them.

Usage:
    python scripts/check_line_endings.py              # scan tracked files
    python scripts/check_line_endings.py --staged     # scan staged content

Exit code 0 = clean, 1 = a file has CRLF line endings.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Binary / non-text assets carry bytes, not lines, so a CR in them is not a
# line ending. Same set the personal-data check uses.
EXEMPT_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif",
    ".ttf", ".otf",
    ".pdf",
    ".cls",  # vendored LaTeX class files, not ours to normalize
    # The .gitattributes explicitly declares these as `eol=crlf` (Windows
    # batch/PowerShell need CRLF to run), so a CR in them is intended.
    ".bat", ".cmd", ".ps1",
}
EXEMPT_DIRS = {".git"}

# The checker contains the string it looks for, so it exempts itself rather
# than contorting the check.
SELF = "scripts/check_line_endings.py"


def tracked_files(staged: bool) -> list[Path]:
    """The files to scan (mirrors check_no_user_data.py's two modes)."""
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
        if set(p.parts) & EXEMPT_DIRS:
            continue
        if p.relative_to(REPO_ROOT).as_posix() == SELF:
            continue
        if p.suffix.lower() in EXEMPT_SUFFIXES:
            continue
        paths.append(p)
    return paths


def has_crlf(path: Path) -> bool:
    """True when the file's bytes contain a carriage return.

    Reads bytes (not text) so an over-eager decode cannot mask a CR, and so
    a lone ``\\r`` (old-Mac) is flagged too -- the repo wants LF only.
    """
    data = path.read_bytes()
    return b"\r" in data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true",
                        help="scan staged content instead of tracked files")
    args = parser.parse_args()

    files = tracked_files(args.staged)
    offenders = [p for p in files if has_crlf(p)]

    if offenders:
        print(f"CRLF line endings found in {len(offenders)} file(s):")
        for p in offenders:
            print(f"  {p.relative_to(REPO_ROOT).as_posix()}")
        print("\n.eol=lf is declared; these files were committed with CRLF.")
        print("Re-normalize with:  git add --renormalize .  then commit.")
        return 1

    print(f"Clean: scanned {len(files)} tracked file(s), all LF line endings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
