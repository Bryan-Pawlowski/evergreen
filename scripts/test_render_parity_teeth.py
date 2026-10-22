#!/usr/bin/env python
"""Teeth test for the render-parity check: it must be able to FAIL.

A check that cannot fail is not a check. A slot's *content* is rendered from
the profile — the template's ``%HERMES:SLOT summary`` block is replaced
wholesale by whatever the profile's ``summary`` field renders to — so the
faithful way to break a template is to hand it a profile with an empty
``summary``: the PDF still builds and still has pages (the selftest would pass
it), but the summary text is gone from the layer. This asserts the render-parity
check then reports the summary missing.

The positive half — every shipped template against the sample profile — is
asserted too, so the check is proven both ways.

Usage:
    python scripts/test_render_parity_teeth.py

Exit code 0 = the check both passes on good templates and fails on a profile
with a dropped field. 1 = either half is broken. Needs tectonic (it builds the
PDFs); in CI that is installed in the render-parity job before this step runs.
"""
from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_render_parity import (  # noqa: E402
    _check_template,
    _claims,
    _declared_slots,
)
from evergreen.render import discover_templates  # noqa: E402
from evergreen.selftest import SAMPLE  # noqa: E402


def main() -> int:
    templates = discover_templates(REPO_ROOT / "templates")
    if not templates:
        print("FAIL: no shipped templates discovered")
        return 1

    claims = _claims(SAMPLE)
    ok = True

    # Positive half: every unmodified template passes.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for template in templates:
            findings = _check_template(template, claims, tmp)
            status = "OK" if not findings else "FAIL"
            if findings:
                ok = False
            print(f"  positive [{template.id}] {status}"
                  + (f": {findings}" if findings else ""))

    # Negative half: a profile with an empty summary must be flagged by any
    # template that declares a summary slot. The PDF still builds and still has
    # pages (the selftest would pass it), but the summary text is gone from the
    # layer — the exact gap the check closes.
    if "summary" not in SAMPLE.get("person", {}):
        print("FAIL: sample profile has no person.summary; update this "
              "teeth test")
        return 1
    broken = copy.deepcopy(SAMPLE)
    broken["person"]["summary"] = ""

    summarised = [t for t in templates if "summary" in _declared_slots(t)]
    if not summarised:
        print("FAIL: no shipped template declares a summary slot; update "
              "this teeth test")
        return 1

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for template in summarised:
            findings = _check_template(template, claims, tmp, data=broken)
            if any("summary" in f for f in findings):
                print(f"  negative [{template.id}/empty summary] OK: "
                      f"{findings}")
            else:
                ok = False
                print(f"  negative [{template.id}/empty summary] FAIL: the "
                      "check passed a template with an empty summary")

    if ok:
        print("\nteeth: the render-parity check passes on good templates and "
              "fails on a profile with a dropped field")
        return 0
    print("\nteeth: FAILED — the render-parity check did not behave as required")
    return 1


if __name__ == "__main__":
    sys.exit(main())
