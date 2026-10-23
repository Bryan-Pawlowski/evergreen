#!/usr/bin/env python
"""Teeth test for the desktop half's gate: it must be able to FAIL.

A gate that cannot fail is not a gate, and this one guards a file loaded in the
renderer with the app's full authority — so every rule is proved with a poisoned
sample, one rule at a time:

* each **surface rule** (prototype patching, dynamic code, a dynamic or remote
  import, script injection, app-DOM reach) must be flagged in a minimal sample
  that is otherwise clean, and the real ``desktop/plugin.js`` must produce zero
  findings — otherwise the rule is either dead or crying wolf;
* each **contract check** (the id, the opt-in default, the pane, the palette
  command, the import allowlist, the theme rule, and the ``ctx.rest`` ↔ route
  match) must reject a mutated copy of the real file, so the check is proved
  against the file it actually guards rather than against a fixture;
* the **syntax** step must reject JSX, which is the way a disk plugin actually
  dies: the file is loaded uncompiled.

Usage:
    python scripts/test_desktop_lint_teeth.py

Exit code 0 = every rule bit and the real file is clean. 1 = a rule did not
bite. 2 = node is missing, so the syntax rule cannot be proved here.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_desktop_plugin import (  # noqa: E402
    DECLARED_ENTRY,
    contract_findings,
    desktop_surface_findings,
    plugin_name_from_manifest,
    rest_calls,
    syntax_finding,
)

ENTRY = REPO_ROOT / DECLARED_ENTRY
API = REPO_ROOT / "dashboard" / "plugin_api.py"

# One minimal, otherwise-valid JS sample per surface rule. A list, not a dict:
# two rules share a name, and a dict would silently keep only the last sample.
SURFACE_SAMPLES: list[tuple[str, str]] = [
    ("prototype patching", "Object.defineProperty(console.prototype, 'log', { value: 1 })\n"),
    ("prototype patching", "String.prototype.trim = function () { return this }\n"),
    ("prototype patching", "Reflect.setPrototypeOf(console, {})\n"),
    ("dynamic code evaluation", "const run = eval('1 + 1')\n"),
    ("dynamic code evaluation", "const run = new Function('return 1')\n"),
    ("dynamic import outside the SDK", "const later = () => import('lodash')\n"),
    ("remote import outside the SDK", "import thing from 'https://example.test/thing.js'\n"),
    ("script injection", "const node = document.createElement('script')\n"),
    ("script injection", "const html = '<script src=\"x\"></script>'\n"),
    ("app DOM reach", "const el = document.querySelector('[data-slot=\"composer\"]')\n"),
    ("app DOM reach", "const el = document.getElementById('[data-testid=\"x\"]')\n"),
    ("app DOM reach",
     "new MutationObserver(fn).observe(document.body, { childList: true, subtree: true })\n"),
]


def mutated(source: str, old: str, new: str) -> str:
    """*source* with exactly one substitution, asserting it applied once.

    A teeth test whose mutation silently missed reports "the check passed a
    broken file" when the file was never broken — the failure mode this asserts
    against.
    """
    count = source.count(old)
    if count != 1:
        raise AssertionError(f"the mutation {old!r} matched {count} times, not once")
    return source.replace(old, new, 1)


def main() -> int:
    if not ENTRY.is_file():
        print(f"FAIL: {DECLARED_ENTRY} does not exist, so there is nothing to guard")
        return 1
    if not API.is_file():
        print(f"FAIL: {API} does not exist, so the REST routes cannot be checked")
        return 1

    source = ENTRY.read_text(encoding="utf-8")
    api_source = API.read_text(encoding="utf-8")
    plugin_name = plugin_name_from_manifest(REPO_ROOT)
    failures: list[str] = []

    # -- the real file is clean -------------------------------------------
    surface = desktop_surface_findings(source)
    if surface:
        failures.append(f"the gate flagged the shipped desktop half (surface): {surface}")
    contract = contract_findings(source, plugin_name, api_source)
    if contract:
        failures.append(f"the gate flagged the shipped desktop half (contract): {contract}")

    # -- each surface rule bites ------------------------------------------
    for index, (rule, sample) in enumerate(SURFACE_SAMPLES, start=1):
        findings = desktop_surface_findings(sample)
        hit = " ".join(found_rule for found_rule, _ in findings)
        if rule not in hit:
            failures.append(
                f"surface rule {rule!r} did not fire on sample #{index} "
                f"({sample.strip()!r}); findings were {findings or '(none)'}"
            )
        else:
            print(f"  negative [{rule} #{index}] OK")

    # A comment must not be able to make a rule look satisfied.
    if desktop_surface_findings("// eval('1') is forbidden\nconst ok = 1\n"):
        failures.append("a commented-out eval was reported as a dynamic-code finding")
    # ...but a string is not a comment: it is where a payload lives, so a
    # construct inside one still fires.
    if not desktop_surface_findings("const html = 'createElement(\"script\")'\n"):
        failures.append("a script-injection payload inside a string was not flagged")
    # And ordinary code must stay clean, or every rule above is meaningless.
    if desktop_surface_findings("const total = 1 + 1\nconst label = `${total}`\n"):
        failures.append("the surface lint flags ordinary code")

    # -- each contract check bites ----------------------------------------
    contract_cases: list[tuple[str, str, str]] = [
        ("a drifted id", "id: 'evergreen'", "id: 'evergreen-typo'"),
        ("defaultEnabled missing", "  defaultEnabled: false,\n", ""),
        ("defaultEnabled true", "defaultEnabled: false", "defaultEnabled: true"),
        ("no pane", "area: PANES_AREA", "area: 'statusBar.right'"),
        ("no palette command", "area: PALETTE_AREA", "area: 'statusBar.left'"),
        ("pane without a width", "width: '420px'", "width: undefined"),
        ("a disallowed import", "from 'react/jsx-runtime'", "from 'lodash-es'"),
        ("a hardcoded colour", "style: { height: '2px', opacity: 0.55 }",
         "style: { height: '2px', opacity: 0.55, color: '#ff0000' }"),
        ("a REST path the door does not serve",
         "ctx.rest('/workspaces')", "ctx.rest('/workspace')"),
        ("a REST method the door does not serve",
         "  const generate = async () => {",
         "  const generate = async () => {\n    await ctx.rest('/generate')"),
    ]
    for label, old, new in contract_cases:
        try:
            broken = mutated(source, old, new)
        except AssertionError as exc:
            failures.append(f"{label}: could not build the poisoned copy ({exc})")
            continue
        findings = contract_findings(broken, plugin_name, api_source)
        if not findings:
            failures.append(f"{label}: the contract check passed a broken file")
        else:
            print(f"  negative [{label}] OK: {findings[0]}")

    # -- the syntax step bites --------------------------------------------
    try:
        error, _detail = syntax_finding(source)
    except RuntimeError as exc:
        print(f"SKIP: {exc}")
        return 2
    if error:
        failures.append(f"the shipped desktop half does not parse: {error}")

    jsx_sample = source.replace("return jsxs('div', {", "return <div>{", 1)
    try:
        jsx_error, _detail = syntax_finding(jsx_sample)
    except RuntimeError:
        return 2
    if not jsx_error:
        failures.append("the syntax step passed a file written with JSX syntax")
    else:
        print("  negative [JSX syntax] OK")

    # -- and the REST calls the pane really makes -------------------------
    for path, method in rest_calls(source):
        print(f"  positive [{method} {path}] matched to a route")

    if failures:
        print("\nteeth: FAILED")
        for failure in failures:
            print(f"  FAIL: {failure}")
        return 1

    print(
        f"\nteeth: the desktop-half gate is clean on the shipped file, flags "
        f"{len(SURFACE_SAMPLES)} surface constructs and {len(contract_cases)} "
        f"contract mutations, and rejects JSX syntax"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
