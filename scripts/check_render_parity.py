#!/usr/bin/env python
"""Render every shipped template and prove its content reaches the PDF.

The selftest compiles each template and checks that a PDF with pages exists,
but it never inspects the PDF's *text layer*. A template can compile to a
one-page PDF that is visually blank, or that silently drops a whole slot (the
minimal dialect once dropped a language entry's level, so the list of
languages vanished from the PDF while the profile still said it was there).
This closes that gap.

For each shipped template it:
  * renders the sample profile (the same one the selftest uses),
  * compiles it with tectonic,
  * reads the PDF's text layer (poppler's pdftotext, falling back to pymupdf),
  * and asserts that every slot the template *declares* carries its content
    into that text layer.

Three normalisations keep this honest rather than brittle:

  * Markup stripped. Profile values carry markdown (``**CUDA**``,
    ``_rendering_``) that the template converts to LaTeX. The PDF text layer
    holds the *plain* result, so claims are run through ``markup.plain``
    before matching; otherwise every bold/italic claim false-fails.
  * Casefold. YaAC sets most text in small caps / letterspacing, so "Acme
    Corp" extracts as "ACME CORP". Casefolding both sides still catches a
    genuinely missing claim.
  * Per-declared-slot scoping. Each template only promises the slots it
    declares. ``plain`` is the one-column bootstrap and declares no
    ``languages`` slot, so a ``languages``-content check must not fail it. A
    template that is *missing a slot it declares* still fails.

The checks are string presence, not layout geometry -- geometry is the
selftest's job (overfull boxes, page count). This script only asks: is the
content in the PDF at all?

Usage:
    python scripts/check_render_parity.py            # every shipped template
    python scripts/check_render_parity.py minimal    # one, by id

Exit code 0 = every template's declared content is present. 1 = a build
failed or content is missing. 2 = tectonic is not installed (a real
failure: we cannot prove the PDF without it).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# The sample profile is the selftest's, so this stays in lock-step with the
# end-to-end test: the same facts, checked one layer deeper.
from evergreen.selftest import SAMPLE  # noqa: E402
from evergreen.career import Workspace  # noqa: E402
from evergreen.markup import plain as _markup_plain  # noqa: E402
from evergreen.render import discover_templates, render_tex, stage  # noqa: E402
from evergreen.build import build_tex, find_tectonic  # noqa: E402


def _norm(text: str) -> str:
    """The single normalisation applied to both claims and the PDF text.

    Strip markup, replace ``&`` with " and " (a template may render the
    ampersand as a word), casefold, and collapse whitespace runs to single
    spaces. Running *both* sides through the same transform keeps the
    substring test stable across templates that re-case, re-lay-out, or
    re-space the same content.
    """
    text = _markup_plain(text or "")
    text = text.replace("&", " and ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _claims(content: dict) -> dict[str, list]:
    """Map each slot to the content that must survive into the PDF.

    Every value is a *known constant* in the sample profile, read from
    ``content`` (never re-typed) so the check stays in lock-step with the
    selftest. Two shapes of claim:

      * a ``str`` -- must be present (all of them are AND), or
      * a ``list[str]`` -- an *any-of* group: the check passes if any member
        is present. Used for links, where a template shows either the label
        ("testuser") or the URL host ("github.com"); either proves the link
        rendered.
    """
    person = content["person"]

    claims: dict[str, list] = {
        "identity": [
            f"{person['name']['first']} {person['name']['last']}",
            person["tagline"],
            person["email"],
            person["location"],
        ],
        "summary": [person["summary"]],
        "experience": [e["org"] for e in content["experience"]],
        "projects": [p["title"] for p in content["projects"]],
        "education": (
            [e["degree"] for e in content["education"]]
            + [e["org"] for e in content["education"]]
        ),
        "skills": [s["label"] for s in content["skills"]],
        "milestones": [m["title"] for m in content["milestones"]],
        "languages": [l["name"] for l in content["languages"]]
        + [l["level"] for l in content["languages"]],
        "links": [],
    }

    # Experience bullets: the first bullet of each entry, so a template that
    # renders the role but drops its bullets is caught. (We do not claim every
    # bullet -- a template legitimately abridges to the first one or two.)
    for entry in content["experience"]:
        bullets = entry.get("bullets") or []
        if bullets:
            claims["experience"].append(bullets[0].get("text", ""))

    # Skills: the items are the real content (the labels alone are too weak --
    # "Languages" / "Systems" are common words that could appear as section
    # headers).
    claims["skills"] += [
        item for s in content["skills"] for item in s["items"]
    ]

    # Links: an any-of group per link -- the label, or the URL host. Only
    # templates that *declare* a links slot reach here (see the per-slot
    # scoping in _check_template), so a template that renders the link is
    # proven to carry it, whether as a label or a URL.
    for link in person.get("links") or []:
        label = (link.get("label") or "").strip()
        host = urlparse(link.get("url", "")).netloc.casefold()
        group = [g for g in (label, host) if g]
        if group:
            claims["links"].append(group)

    # Normalise every claim once; drop empties (and empty any-of groups).
    out: dict[str, list] = {}
    for slot, entries in claims.items():
        normalised = []
        for entry in entries:
            if isinstance(entry, list):  # any-of group
                members = [c for c in (_norm(g) for g in entry) if c]
                if members:
                    normalised.append(members)
            else:
                c = _norm(entry)
                if c:
                    normalised.append(c)
        out[slot] = normalised
    return out


def _read_text_layer(pdf: Path) -> str:
    """Read a PDF's text layer. poppler's pdftotext first, pymupdf as a
    fallback so the job works on any runner that has one of the two."""
    if shutil.which("pdftotext"):
        proc = subprocess.run(
            ["pdftotext", str(pdf), "-"],
            capture_output=True, text=True, errors="replace",
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf))
        return "\n".join(page.get_text() for page in doc)
    except Exception as exc:  # pragma: no cover - depends on the runner
        return f"<no text layer could be read: {exc}>"


def _declared_slots(template) -> set[str]:
    """The standard slot names a template declares. Custom ``section:<id>``
    slots are skipped: they carry no standard content to check."""
    slots = set()
    for marker in re.finditer(r"%HERMES:SLOT\s+([A-Za-z0-9_:.-]+)",
                              template.source):
        name = marker.group(1)
        if not name.startswith("section:"):
            slots.add(name)
    return slots


def _profile(tmp: Path, data: dict | None = None):
    """Load a profile through a real workspace, so ``render_tex`` gets a
    CareerProfile object (it is not a plain dict). ``data`` defaults to the
    sample; the teeth test passes a mutated copy of it."""
    payload = data if data is not None else SAMPLE
    ws = Workspace(tmp / "career")
    ws.create()
    ws.career_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return ws.load()


def _check_template(template, claims: dict[str, list], tmp: Path,
                    out_dir: Path | None = None,
                    data: dict | None = None) -> list[str]:
    """Build one template and return the findings (empty when the declared
    content is all present). ``data`` is the profile dict (defaults to the
    sample); the teeth test passes a mutated copy to prove the check fails.
    When ``out_dir`` is given, the compiled PDF is also copied there as
    ``<id>.pdf`` so a caller (CI) can upload it."""
    build_dir = tmp / template.id
    try:
        profile = _profile(tmp, data)
        tex = render_tex(template, profile)
        main_tex = stage(template, build_dir)
        main_tex.write_text(tex, encoding="utf-8", newline="\n")
        fontconfig = None
        if template.fontconfig and (build_dir / template.fontconfig).exists():
            fontconfig = str((build_dir / template.fontconfig).resolve())
        result = build_tex(main_tex, build_dir,
                           engine=template.engine, fontconfig=fontconfig)
    except Exception as exc:
        return [f"render/build raised: {exc}"]

    if not result.ok:
        return [f"build failed: {'; '.join(result.errors)[:300]}"]
    if not result.pdf:
        return ["build reported ok but produced no PDF"]

    # Retain the compiled PDF for inspection / artifact upload.
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result.pdf, out_dir / f"{template.id}.pdf")

    text = _read_text_layer(result.pdf)
    if not text.strip():
        return ["PDF has no readable text layer (empty extraction)"]

    findings = []
    haystack = _norm(text)
    # Only check the slots this template actually declares, so a template that
    # intentionally omits a slot (plain's bootstrap has no languages) is not
    # penalised for it.
    for slot in sorted(_declared_slots(template)):
        for claim in claims.get(slot, []):
            if isinstance(claim, list):
                # any-of group: any member present is enough.
                if not any(member in haystack for member in claim):
                    findings.append(
                        f"{slot}: none of {claim!r} present")
            elif claim not in haystack:
                findings.append(f"{slot}: missing {claim!r}")
    return findings


def _parse_args(argv: list[str]) -> tuple[set[str], Path | None]:
    """Parse the argv: optional template id(s) plus an optional ``--out DIR``.

    ``--out`` is how CI collects the compiled PDFs for artifact upload; without
    it the PDFs live only in the temp build dir and are discarded.
    """
    only: set[str] = set()
    out: Path | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--out":
            if i + 1 >= len(argv):
                raise SystemExit("FAIL: --out needs a directory argument")
            out = Path(argv[i + 1])
            i += 2
            continue
        only.add(arg)
        i += 1
    return only, out


def main() -> int:
    only, out_dir = _parse_args(sys.argv[1:])
    claims = _claims(SAMPLE)
    templates = discover_templates(REPO_ROOT / "templates")
    if not templates:
        print("FAIL: no shipped templates were discovered")
        return 1

    if only:
        templates = [t for t in templates if t.id in only]
        if not templates:
            print(f"FAIL: no template matches {sorted(only)}")
            return 1

    if not find_tectonic():
        print("FAIL: tectonic is not installed; cannot prove the PDFs exist.")
        print("      (In CI it is installed in the step before this one.)")
        return 2

    print(f"render-parity: {len(templates)} template(s): "
          f"{sorted(t.id for t in templates)}")

    failed: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for template in templates:
            findings = _check_template(template, claims, tmp, out_dir=out_dir)
            if findings:
                failed[template.id] = findings
                print(f"  [{template.id}] FAIL ({len(findings)}):")
                for finding in findings:
                    print(f"        - {finding}")
            else:
                print(f"  [{template.id}] OK")

    if failed:
        print(f"\nrender-parity: {len(failed)} of {len(templates)} template(s) "
              f"missing declared content")
        return 1
    print(f"\nrender-parity: all {len(templates)} templates carry their "
          f"declared content")
    return 0


if __name__ == "__main__":
    sys.exit(main())
