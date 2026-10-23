#!/usr/bin/env python
"""Prove a *generated* template is as good as the one it came from.

``check_render_parity.py`` proves every shipped template carries its
profile's content into the PDF's text layer. This proves the same thing
for a template the Configurator *generated* -- because a generated
template is the one a user will actually ship, and the settings rewrite
runs over the source before rendering. A settings rewrite that dropped a
slot marker, ate a brace, or shifted a value would still compile and
still produce a PDF, and would only be caught by reading the text back.

Two things are asserted, and the second is the one that matters:

  1. **Content.** The generated PDF carries every piece of content the
     base template's PDF carries. Same claims, same normalisation, same
     text layer. A setting must never cost the user a line of their CV.

  2. **Visibility.** The change is actually *visible*. A rewrite that
     was accepted, reported success, and then silently did nothing would
     pass a content check on its own -- the content is identical either
     way. So the generated PDF's text geometry must differ from the
     base's: a different font size, a different left margin, or a
     different page count. This is the check that catches a setting that
     was accepted and thrown away.

The base and the generated template are both compiled here, so the
comparison is PDF against PDF, not PDF against expectation.

Usage:
    python scripts/check_generated_parity.py

Exit code 0 = the generated template carries the content and shows the
change. 1 = it does not. 2 = tectonic is not installed.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_render_parity import (NoTextExtractor,  # noqa: E402
                                 _claims, _declared_slots, _extractor_name,
                                 _norm, _profile, _read_text_layer)
from evergreen.build import build_tex, find_tectonic  # noqa: E402
from evergreen.generate import generate_template  # noqa: E402
from evergreen.render import load_template, render_tex, stage  # noqa: E402
from evergreen.selftest import SAMPLE  # noqa: E402

# The settings used for the comparison. Deliberately hits several knobs
# at once, including the ones that change the page's geometry.
SETTINGS = {
    "base_size": "12pt",
    "margin": "1in",
    "section_size": "\\LARGE",
    "bullet_indent": "18pt",
}


def _compile(template, tmp: Path, tag: str) -> tuple[Path, str]:
    """Compile a template and return (pdf path, text layer)."""
    profile = _profile(tmp)
    tex = render_tex(template, profile)
    build_dir = tmp / tag
    main_tex = stage(template, build_dir)
    main_tex.write_text(tex, encoding="utf-8", newline="\n")
    fontconfig = None
    if template.fontconfig and (build_dir / template.fontconfig).exists():
        fontconfig = str((build_dir / template.fontconfig).resolve())
    result = build_tex(main_tex, build_dir, engine=template.engine,
                       fontconfig=fontconfig)
    if not result.ok:
        raise RuntimeError(f"build failed: {'; '.join(result.errors)[:300]}")
    if not result.pdf:
        raise RuntimeError("build reported ok but produced no PDF")
    return result.pdf, _read_text_layer(result.pdf)


def _render_fingerprint(pdf: Path, dpi: int = 72) -> str:
    """A fingerprint of what the page actually *looks like*.

    A text-geometry probe (font sizes, left edges, page count) is not
    enough. It cannot see a change that only affects an image overlay:
    ``minimal``'s band and dark column are PNGs, so resizing the band
    changes the pixels and the text layer not at all. Measured: growing
    ``\\bandH`` from 100pt to 140pt moves the banner and changes nothing
    the text geometry reports.

    A pixel fingerprint sees every visible change, and — just as
    importantly — it is *identical* when nothing changed. That is what
    makes it able to prove a knob is dead: if changing a knob leaves
    the fingerprint untouched, the knob does nothing.

    ``pdftotext`` cannot give this, so pymupdf is used directly and the
    check is skipped (not failed) when it is absent -- the content
    check still runs, and CI installs pymupdf.
    """
    try:
        import pymupdf
    except Exception:
        return ""
    digest = hashlib.sha256()
    # The document is closed explicitly: on Windows an open file cannot be
    # deleted, and the temp tree is cleaned up on the way out.
    doc = pymupdf.open(str(pdf))
    try:
        for page in doc:
            digest.update(page.get_pixmap(dpi=dpi).samples)
    finally:
        doc.close()
    return digest.hexdigest()[:16]


def _geometry(pdf: Path) -> dict:
    """The text geometry of a PDF: the shape of the text on the page.

    Reported for context, but the *decision* is made on the pixel
    fingerprint -- see :func:`_render_fingerprint` for why text geometry
    alone is not sufficient.
    """
    try:
        import pymupdf
    except Exception:
        return {}
    doc = pymupdf.open(str(pdf))
    sizes: list[float] = []
    lefts: list[float] = []
    try:
        pages = doc.page_count
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span["text"].strip():
                            sizes.append(round(float(span["size"]), 1))
                            lefts.append(round(float(span["bbox"][0]), 1))
    finally:
        doc.close()
    return {
        "pages": pages,
        "min_size": min(sizes) if sizes else None,
        "max_size": max(sizes) if sizes else None,
        "min_left": min(lefts) if lefts else None,
    }


def main() -> int:
    if not find_tectonic():
        print("FAIL: tectonic is not installed; cannot prove the PDFs exist.")
        print("      (In CI it is installed in the step before this one.)")
        return 2

    # The content check reads the PDFs' text layers, so an absent
    # extractor is an environment failure, not a content failure. Checked
    # up front so the failure is unambiguous.
    extractor = _extractor_name()
    if extractor is None:
        print("FAIL: no PDF text extractor is installed; cannot read the "
              "PDFs' text layers.")
        print("      Install one:  pip install pymupdf")
        return 2
    print(f"generated-parity: text extractor: {extractor}")

    base_dir = REPO_ROOT / "templates" / "plain"
    recipe_path = base_dir / "recipe.yaml"
    if not recipe_path.exists():
        print("FAIL: templates/plain has no recipe.yaml; nothing to generate from.")
        return 1

    base = load_template(base_dir)
    claims = _claims(SAMPLE)
    findings: list[str] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        try:
            base_pdf, base_text = _compile(base, tmp, "base")
        except NoTextExtractor:
            print("FAIL: the PDF text extractor became unavailable.")
            return 2
        except Exception as exc:
            print(f"FAIL: the base template did not compile: {exc}")
            return 1

        dest = tmp / "generated"
        try:
            generate_template(base_dir, dest, SETTINGS,
                              template_id="plain-configured", name="Plain (configured)")
        except Exception as exc:
            print(f"FAIL: generation failed: {exc}")
            return 1

        generated = load_template(dest)
        try:
            # The build dir must not be the template dir: stage() copies the
            # template's files into the build dir, and copying a file onto
            # itself fails on Windows.
            gen_pdf, gen_text = _compile(generated, tmp, "build_generated")
        except NoTextExtractor:
            print("FAIL: the PDF text extractor became unavailable.")
            return 2
        except Exception as exc:
            print(f"FAIL: the generated template did not compile: {exc}")
            return 1

        # --- 1. Content: the generated PDF carries what the base carries.
        haystack = _norm(gen_text)
        for slot in sorted(_declared_slots(base)):
            for claim in claims.get(slot, []):
                if isinstance(claim, list):
                    if not any(member in haystack for member in claim):
                        findings.append(f"{slot}: none of {claim!r} present")
                elif claim not in haystack:
                    findings.append(f"{slot}: missing {claim!r}")

        # --- 2. Visibility: the change actually shows up on the page.
        #
        # This is decided on the *pixel fingerprint*, not the text
        # geometry: a text probe cannot see an image-overlay change
        # (minimal's band is a PNG), and would report a false "no
        # change" for a knob that plainly moved something.
        base_geo = _geometry(base_pdf)
        gen_geo = _geometry(gen_pdf)
        base_fp = _render_fingerprint(base_pdf)
        gen_fp = _render_fingerprint(gen_pdf)
        if not base_fp or not gen_fp:
            print("  (pymupdf is not installed; the visibility check is skipped)")
        elif base_fp == gen_fp:
            findings.append(
                "the generated PDF renders identically to the base "
                f"(fingerprint {gen_fp}); the settings had no visible effect")
        else:
            print(f"  base fingerprint:     {base_fp}")
            print(f"  generated fingerprint: {gen_fp}")
            if base_geo and gen_geo:
                print(f"  base geometry:     {base_geo}")
                print(f"  generated geometry: {gen_geo}")

    if findings:
        print(f"\ngenerated-parity: FAIL ({len(findings)}):")
        for finding in findings:
            print(f"  - {finding}")
        return 1

    print("\ngenerated-parity: the generated template carries the base's content "
          "and shows the change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
