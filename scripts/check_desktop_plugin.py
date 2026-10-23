#!/usr/bin/env python
"""The desktop half's gate: the SDK surface, the package contract, and syntax.

`hermes plugins validate` is the authoritative gate, and it lints
``desktop/plugin.js`` through ``hermes_cli/plugin_validate_desktop.py``. CI
cannot run it: it needs a Hermes install new enough to have ``validate``, and
the PyPI release lags the git install. This script is the self-contained
subset, so a commit is witnessed on any machine with Python and (for the
syntax step) node.

Three kinds of check, in the order they are worth reading:

**1. The surface lint.** A disk plugin is evaluated as ESM in the renderer
realm with the app's full authority; the loader gives error isolation, not a
capability boundary. The rules at admission refuse the moves that step outside
the SDK surface, and this ports them: prototype patching, ``eval`` /
``new Function``, a dynamic ``import()`` of anything but the SDK, a static
import with a URL scheme, ``createElement('script')`` / ``<script>``, and
reaching into the app's own markup through ``[data-slot|tour|sidebar|testid]``
or a body-wide ``MutationObserver``.

One rule is **stricter than upstream on purpose**: upstream permits
``X.prototype.y =`` where ``X`` is provably the file's own class, because a
minified library setting fields on its own classes is not reaching into the
renderer. A hand-written plugin is not a bundle, so here every prototype write
is a finding. A tripwire that refuses one false positive costs a comment; a
tripwire that excuses a renderer patch costs the boundary.

**2. The package contract.** The things that only go wrong in the field:

* the file's ``id`` must equal ``plugin.yaml``'s ``name`` — the Electron shell
  copies the half into ``desktop-plugins/<id>/``, and a plugin whose ``id``
  does not match its folder is a plugin the app will not load;
* ``defaultEnabled: false`` — a desktop half ships **opt-in** (§1 of the plan):
  dropping the package into ``plugins/`` must change nothing the user can see
  until they say so;
* a pane contribution (``area: PANES_AREA``) with a ``placement`` and a
  ``width``, and a palette command (``area: PALETTE_AREA``) — the two surfaces
  the milestone is defined by;
* **every ``ctx.rest()`` path must be a route in ``dashboard/plugin_api.py``**,
  with the method the pane sends. This is the check that earns its keep: the
  pane and the door are edited in different languages, and a renamed route
  fails as a 404 in a running app, not in CI;
* only ``@hermes/plugin-sdk``, ``react`` and ``react/jsx-runtime`` are imported
  (the loader refuses anything else at load time);
* no hardcoded colours — ``#rrggbb`` / ``rgb()`` / ``hsl()`` in the pane is a
  colour that will not follow the user's theme; the values a *knob* carries
  are data and never appear as literals.

**3. Syntax.** The file is loaded uncompiled, so JSX syntax does not parse —
the most likely way a plugin dies before it renders anything. ``node --check``
on a copy named ``.mjs`` parses it as an ES module. Node ships on every GitHub
runner and on any developer machine that has the desktop app's tooling. When it
is absent this exits **2** and says so, because a syntax check that silently
does not run is not a check.

Usage:  python scripts/check_desktop_plugin.py [--dir <plugin root>]
Exit 0 = ok, 1 = findings, 2 = the environment cannot run the check.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DECLARED_ENTRY = "desktop/plugin.js"
API_MODULE = "dashboard/plugin_api.py"

ALLOWED_IMPORTS = frozenset({"@hermes/plugin-sdk", "react", "react/jsx-runtime"})

# The pane the plan specifies: docked right, ~420px, plus a ⌘K command.
PANE_AREA = ("PANES_AREA", "panes")
PALETTE_AREA = ("PALETTE_AREA", "palette")
PANE_WIDTH = "420px"


# --------------------------------------------------------------------------
# 1. The surface lint (ported from hermes_cli/plugin_validate_desktop.py)
# --------------------------------------------------------------------------

# (rule, regex). Every hit fails the check.
FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("prototype patching",
     re.compile(r"\bObject\.definePropert(?:y|ies)\(\s*[\w$.]+\.prototype\b")),
    ("prototype patching",
     re.compile(r"\b(?:Reflect|Object)\.setPrototypeOf\(|\.__proto__\s*=")),
    ("dynamic code evaluation",
     re.compile(r"(?<![\w$.])eval\(|\bnew\s+Function\(")),
    ("dynamic import outside the SDK",
     re.compile(r"\bimport\(\s*(?!['\"](?:@hermes/plugin-sdk|react)(?:/[\w/-]*)?['\"]\s*\))")),
    ("remote import outside the SDK",
     re.compile(r"\bimport\s+(?:[^;'\"]*?\bfrom\s*)?['\"][a-zA-Z][\w+.-]*:")),
    ("script injection",
     re.compile(r"createElement\(\s*['\"]script['\"]\s*\)|<script\b")),
    ("app DOM reach",
     re.compile(r"\bdocument\.(?:querySelector(?:All)?|getElementsBy\w+|getElementById)\(\s*[`'\"][^`'\"]*"
                r"\[data-(?:slot|tour|sidebar|testid)\b")),
    ("app DOM reach",
     re.compile(r"\.observe\(\s*document\.body\s*,\s*\{[^}]*\b(?:childList|subtree)\b")),
)

# A ``X.prototype.y =`` assignment on ANY receiver (see the docstring: stricter
# than upstream, which excuses a file's own class).
PROTOTYPE_WRITE = re.compile(r"\b([A-Za-z_$][\w$]*)\.prototype\.[\w$]+\s*=[^=]")

# Strings are matched FIRST and kept, so a ``/*`` or ``//`` inside a string (a
# glob, a URL) cannot open a "comment" that blanks the rest of the file and
# hides whatever forbidden construct sits after it.
_COMMENT = re.compile(
    r"(\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)"
    r"|/\*.*?\*/|(?<![:%\w])//[^\n]*",
    re.DOTALL,
)


def strip_comments(source: str) -> str:
    """*source* with comments blanked, string literals kept (upstream's rule)."""
    return _COMMENT.sub(lambda m: m.group(1) or "\n" * m.group(0).count("\n"), source)


def desktop_surface_findings(source: str) -> list[tuple[str, int]]:
    """``[(rule, line)]`` for every construct outside the plugin SDK surface."""
    stripped = strip_comments(source)
    findings = [
        ("prototype patching", stripped.count("\n", 0, match.start()) + 1)
        for match in PROTOTYPE_WRITE.finditer(stripped)
    ]
    for rule, pattern in FORBIDDEN:
        for match in pattern.finditer(stripped):
            findings.append((rule, stripped.count("\n", 0, match.start()) + 1))
    return sorted(findings, key=lambda finding: finding[1])


# --------------------------------------------------------------------------
# 2. The package contract
# --------------------------------------------------------------------------

_IMPORT_FROM = re.compile(r"\bimport\s+(?:[^;'\"]*?\bfrom\s*)?['\"]([^'\"]+)['\"]")
_PLUGIN_ID = re.compile(r"\bid\s*:\s*['\"]([a-z0-9][a-z0-9._-]{0,63})['\"]")
_ROUTE = re.compile(r"@router\.(get|post|put|delete|patch)\(\s*['\"]([^'\"]+)['\"]")
_HARD_COLOUR = re.compile(r"#[0-9A-Fa-f]{3,8}\b|\brgba?\(|\bhsla?\(")
_DEFAULT_ENABLED = re.compile(r"\bdefaultEnabled\s*:\s*(true|false)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def declared_id(source: str) -> str | None:
    """The plugin id the desktop half declares, or None.

    The first ``id: '…'`` after ``export default {`` is the plugin's own id;
    contribution ids inside ``register()`` come later, so anchoring on the
    default export is what keeps this from matching a pane's id.
    """
    start = source.find("export default")
    if start < 0:
        return None
    match = _PLUGIN_ID.search(source, start)
    return match.group(1) if match else None


def imports_of(source: str) -> list[str]:
    """Every static import specifier in a module."""
    return _IMPORT_FROM.findall(source)


def rest_calls(source: str) -> list[tuple[str, str]]:
    """``[(path, method)]`` for every ``ctx.rest(...)`` call.

    The options object is walked with a paren matcher rather than a regex, so a
    ``method: 'POST'`` after a nested ``body: {…}`` is still found — the order
    of the options must not decide whether the check works.
    """
    calls: list[tuple[str, str]] = []
    # Whitespace between ``ctx``, the dot and ``rest`` is allowed on purpose: a
    # call broken over lines is still a call, and a regex that only matched
    # ``ctx.rest(`` on one line silently skipped every method chained onto a new
    # line — a REST path the door does not serve would then pass unnoticed.
    for match in re.finditer(r"\bctx\s*\.\s*rest\(", source):
        index = match.end()
        depth = 1
        while index < len(source) and depth:
            char = source[index]
            if char in "'\"`":
                index += 1
                while index < len(source) and source[index] != char:
                    index += 2 if source[index] == "\\" else 1
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        call = source[match.end():index]
        path_match = re.match(r"\s*['\"]([^'\"]+)['\"]", call)
        if not path_match:
            continue
        method_match = re.search(r"\bmethod\s*:\s*['\"]([a-zA-Z]+)['\"]", call)
        calls.append((path_match.group(1), (method_match.group(1) if method_match else "GET").upper()))
    return calls


def routes_of(api_source: str) -> list[tuple[str, str]]:
    """``[(method, path)]`` declared by the backend router."""
    return [(method.upper(), path) for method, path in _ROUTE.findall(api_source)]


def area_registered(source: str, candidates: tuple[str, ...]) -> bool:
    """Whether a contribution names one of *candidates* as its ``area``."""
    for candidate in candidates:
        if re.search(r"\barea\s*:\s*" + re.escape(candidate) + r"\b", source):
            return True
        if re.search(r"\barea\s*:\s*['\"]" + re.escape(candidate) + r"['\"]", source):
            return True
    return False


def contract_findings(source: str, plugin_name: str, api_source: str | None) -> list[str]:
    """The package-contract failures for one ``desktop/plugin.js``."""
    errors: list[str] = []

    declared = declared_id(source)
    if not declared:
        errors.append(f"{DECLARED_ENTRY}: no plugin id. The default export must declare `id`.")
    elif plugin_name and declared != plugin_name:
        errors.append(
            f"{DECLARED_ENTRY}: id {declared!r} does not match plugin.yaml's "
            f"{plugin_name!r}. The shell copies this half into "
            f"desktop-plugins/<id>/, so the two must be the same."
        )

    enabled = _DEFAULT_ENABLED.search(source)
    if not enabled:
        errors.append(
            f"{DECLARED_ENTRY}: no `defaultEnabled`. A desktop half ships opt-in "
            f"(defaultEnabled: false), so installing the package changes nothing "
            f"the user can see until they enable it."
        )
    elif enabled.group(1) != "false":
        errors.append(
            f"{DECLARED_ENTRY}: `defaultEnabled: true` — a desktop half must ship "
            f"opt-in. Dropping the package into plugins/ is inert on every surface "
            f"until the user toggles it."
        )

    if not area_registered(source, PANE_AREA):
        errors.append(f"{DECLARED_ENTRY}: no pane contribution (area: PANES_AREA).")
    else:
        if not re.search(r"\bplacement\s*:\s*['\"][a-z]+['\"]", source):
            errors.append(f"{DECLARED_ENTRY}: the pane has no `placement`.")
        if PANE_WIDTH not in source:
            errors.append(
                f"{DECLARED_ENTRY}: the pane does not declare `width: '{PANE_WIDTH}'`; "
                f"a pane without a width claims half the zone."
            )

    if not area_registered(source, PALETTE_AREA):
        errors.append(f"{DECLARED_ENTRY}: no palette command (area: PALETTE_AREA).")

    for specifier in imports_of(source):
        if specifier not in ALLOWED_IMPORTS:
            errors.append(
                f"{DECLARED_ENTRY}: imports {specifier!r}. A disk plugin may import "
                f"only {', '.join(sorted(ALLOWED_IMPORTS))} — the loader refuses anything else."
            )

    for match in _HARD_COLOUR.finditer(strip_comments(source)):
        line = strip_comments(source).count("\n", 0, match.start()) + 1
        errors.append(
            f"{DECLARED_ENTRY}:{line}: hardcoded colour {match.group(0)!r}. Style with "
            f"theme variables (var(--ui-*)) so the pane follows the user's theme."
        )

    if api_source is None:
        errors.append(f"{DECLARED_ENTRY}: calls ctx.rest(), but {API_MODULE} does not exist.")
    else:
        routes = routes_of(api_source)
        known = {path for _, path in routes}
        for path, method in rest_calls(source):
            bare = path.split("?", 1)[0]
            if bare not in known:
                errors.append(
                    f"{DECLARED_ENTRY}: ctx.rest({path!r}) — {API_MODULE} has no route "
                    f"{bare!r}. Routes: {', '.join(sorted(known)) or '(none)'}."
                )
                continue
            methods = {route_method for route_method, route_path in routes if route_path == bare}
            if method not in methods:
                errors.append(
                    f"{DECLARED_ENTRY}: ctx.rest({path!r}) sends {method}, but {API_MODULE} "
                    f"registers {bare!r} for {', '.join(sorted(methods))}."
                )

    return errors


# --------------------------------------------------------------------------
# 3. Syntax (node --check, as an ES module)
# --------------------------------------------------------------------------

def syntax_finding(source: str) -> tuple[str | None, str | None]:
    """``(error, detail)``: a syntax error, or (None, None) when it parses.

    The copy is named ``.mjs`` so node parses it as a module — the same way the
    app loads it — and JSX syntax (which the disk door cannot parse) fails here
    rather than in a toast.
    """
    node = shutil.which("node")
    if not node:
        raise RuntimeError(
            "node was not found on PATH, so the plugin cannot be syntax-checked. "
            "The file is loaded uncompiled, so a syntax error (JSX is the usual one) "
            "only surfaces as a load failure in the app. Install node, or run this "
            "check where the desktop app's tooling lives."
        )

    with tempfile.TemporaryDirectory(prefix="evergreen-desktop-") as workdir:
        probe = Path(workdir) / "plugin.mjs"
        probe.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [node, "--check", str(probe)],
            capture_output=True, text=True, errors="replace",
        )
    if result.returncode == 0:
        return None, None
    detail = (result.stderr or result.stdout).replace(str(probe), DECLARED_ENTRY).strip()
    return "does not parse as an ES module", detail


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def plugin_name_from_manifest(plugin_dir: Path) -> str:
    """``plugin.yaml``'s name, read without a YAML dependency."""
    manifest = plugin_dir / "plugin.yaml"
    if not manifest.is_file():
        return ""
    match = re.search(r"^name:\s*['\"]?([A-Za-z0-9._-]+)['\"]?\s*$",
                      _read(manifest), re.MULTILINE)
    return match.group(1) if match else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the desktop half.")
    parser.add_argument("--dir", default=str(REPO_ROOT),
                        help="plugin root (default: this repo)")
    args = parser.parse_args()

    plugin_dir = Path(args.dir).resolve()
    entry = plugin_dir / DECLARED_ENTRY
    api_path = plugin_dir / API_MODULE

    if not entry.is_file():
        print(f"FAIL  {DECLARED_ENTRY} is missing; the package's desktop half has no entry.")
        return 1

    source = _read(entry)
    plugin_name = plugin_name_from_manifest(plugin_dir)
    api_source = _read(api_path) if api_path.is_file() else None

    errors: list[str] = []

    surface = desktop_surface_findings(source)
    for rule, line in surface:
        errors.append(f"{DECLARED_ENTRY}:{line}: {rule} — outside the plugin SDK surface.")

    errors.extend(contract_findings(source, plugin_name, api_source))

    try:
        syntax_error, detail = syntax_finding(source)
    except RuntimeError as exc:
        print(f"SKIP? {exc}")
        print("\nCannot check the desktop half's syntax: no node on PATH.")
        return 2
    if syntax_error:
        errors.append(f"{DECLARED_ENTRY}: {syntax_error}:\n{detail}")

    for error in errors:
        print(f"FAIL  {error}")
    if errors:
        print(f"\n{len(errors)} desktop-half error(s).")
        return 1

    calls = rest_calls(source)
    routes = routes_of(api_source) if api_source else []
    print(
        f"OK    {DECLARED_ENTRY} is inside the SDK surface: "
        f"{len(calls)} REST call(s) all matched to {len(routes)} route(s), "
        f"id {declared_id(source)!r}, opt-in, and it parses as an ES module."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
