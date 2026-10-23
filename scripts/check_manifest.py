#!/usr/bin/env python
"""Check plugin.yaml against Hermes' manifest contract, without Hermes.

``hermes plugins validate`` is the authoritative gate, but it needs a
Hermes install new enough to have it — the PyPI release lags the git
install, and a CI job that depends on a specific Hermes version is a
brittle thing to maintain. This is a small, self-contained subset of the
same checks, so CI can run it anywhere.

What it checks (mirroring hermes_cli/plugins_manifest.py):

  - the file parses and is a mapping
  - name / version / description are present
  - version is semver-shaped
  - requires_hermes is a parseable spec
  - provides_tools / provides_hooks are lists of strings
  - every field is one Hermes knows about (unknown fields are forward
    compat surface, so this is a warning, not an error)
  - every declared tool is actually registered by the plugin code
  - every registered tool is declared (Hermes' "declared tools" check
    fails catalog admission on a mismatch)
  - the plugin's own module imports and exposes register(ctx)

Usage:  python scripts/check_manifest.py
Exit 0 = ok, 1 = errors.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "plugin.yaml"
DASHBOARD_DIR = REPO_ROOT / "dashboard"

# The fields Hermes knows about (hermes_cli/plugins_manifest.py,
# _KNOWN_MANIFEST_FIELDS). Unknown fields are forward-compat surface, so
# they warn rather than fail.
KNOWN_FIELDS = {
    "name", "version", "description", "author", "requires_env",
    "provides_tools", "provides_hooks", "kind", "hooks", "label",
    "optional_env", "platforms", "external_dependencies", "pip_dependencies",
    "provides_browser_providers", "provides_web_providers",
    "manifest_version", "api_version", "requires_plugins",
    "python_dependencies", "config_schema", "license", "homepage", "tags",
    "capabilities", "emits", "listens", "hermes", "depends",
    "requires_hermes", "python_runtime", "provides_locales", "requires_auth",
    "oauth",
}

REQUIRED_FIELDS = ("name", "version", "description")

_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_VERSION_SPEC = re.compile(r"^(>=|<=|==|>|<)?\s*\d+(?:\.\d+)*")


def load_manifest() -> dict:
    """Read plugin.yaml."""
    try:
        from ruamel.yaml import YAML  # type: ignore
    except ImportError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            print("No YAML parser available (need ruamel.yaml or PyYAML).")
            raise SystemExit(1) from exc
        return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))

    loader = YAML(typ="safe")
    return loader.load(MANIFEST.read_text(encoding="utf-8"))


def registered_tools() -> set[str]:
    """The tool names the plugin's register() actually registers."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import evergreen
    except Exception as exc:
        print(f"FAIL  cannot import the plugin package: {exc}")
        raise SystemExit(1) from exc

    names = set()
    for tool in getattr(evergreen, "TOOLS", []):
        names.add(tool["name"])
    return names


def check_dashboard_half(name: str, version: str) -> tuple[list[str], list[str]]:
    """The dashboard half's contract, as far as it can be checked without Hermes.

    ``dashboard/manifest.json`` is what makes ``/api/plugins/<id>/`` exist at
    all, and four of its fields are load-bearing in a way nothing else catches:

    * ``name`` is the mount prefix *and* the id the desktop half calls with
      ``ctx.rest``. Hermes mounts the routes at ``/api/plugins/<this name>/``;
      if it drifts from ``plugin.yaml``'s name, the plugin's tools are called
      "evergreen" while the HTTP door answers at another id, and every desktop
      call 404s. The two ids must match.
    * ``api`` must be a relative file inside ``dashboard/``. Hermes refuses an
      absolute or escaping path and never mounts the routes — silently, from
      the user's point of view.
    * ``entry`` must exist. The *web* dashboard loads it as a script on every
      visit; a missing file logs a load failure every time, even though the
      desktop app never loads it.
    * ``version`` should be the package's version, or the Capabilities page
      reports something ``hermes plugins list`` does not.

    Returns ``(errors, warnings)``. No ``dashboard/`` directory is not a
    problem: a plugin may well have no HTTP door.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not DASHBOARD_DIR.is_dir():
        return errors, warnings

    manifest_path = DASHBOARD_DIR / "manifest.json"
    if not manifest_path.is_file():
        errors.append("dashboard/manifest.json: the dashboard/ directory exists but has no manifest")
        return errors, warnings

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"dashboard/manifest.json: does not parse: {exc}")
        return errors, warnings
    if not isinstance(data, dict):
        errors.append("dashboard/manifest.json: must contain an object")
        return errors, warnings

    dash_name = str(data.get("name") or "")
    if not dash_name:
        errors.append("dashboard/manifest.json: name: required (it is the mount prefix)")
    elif name and dash_name != name:
        errors.append(
            f"dashboard/manifest.json: name {dash_name!r} does not match "
            f"plugin.yaml's {name!r}. The name is both the mount prefix "
            f"(/api/plugins/<name>/) and the id the desktop half addresses, "
            f"so the two must be the same."
        )

    dash_version = str(data.get("version") or "")
    if dash_version and version and dash_version != version:
        errors.append(
            f"dashboard/manifest.json: version {dash_version!r} does not match "
            f"plugin.yaml's {version!r}; the two halves are one package."
        )

    for field, default in (("api", None), ("entry", "dist/index.js")):
        value = data.get(field, default)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            errors.append(f"dashboard/manifest.json: {field}: must be a non-empty string")
            continue
        target = Path(value)
        if target.is_absolute() or ".." in target.parts:
            errors.append(
                f"dashboard/manifest.json: {field}: {value!r} must be a relative "
                f"path inside dashboard/ (Hermes refuses anything else)"
            )
            continue
        if not (DASHBOARD_DIR / target).is_file():
            errors.append(f"dashboard/manifest.json: {field}: {value!r} does not exist")

    tab = data.get("tab")
    if tab is not None and not isinstance(tab, dict):
        errors.append("dashboard/manifest.json: tab: must be an object")
    elif isinstance(tab, dict) and tab.get("path") and tab.get("hidden"):
        warnings.append(
            "dashboard/manifest.json: a hidden tab does not need a `path`; it adds no tab"
        )

    return errors, warnings


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    if not MANIFEST.exists():
        print(f"FAIL  no plugin.yaml at {MANIFEST}")
        return 1

    try:
        data = load_manifest()
    except Exception as exc:
        print(f"FAIL  plugin.yaml does not parse: {exc}")
        return 1

    if not isinstance(data, dict):
        print("FAIL  plugin.yaml must contain a mapping")
        return 1

    for field in REQUIRED_FIELDS:
        if not data.get(field):
            errors.append(f"{field}: required and missing")

    name = data.get("name", "")
    if name and not _NAME.match(str(name)):
        errors.append(f"name: {name!r} must be lowercase, digits, dot, dash, underscore")

    version = str(data.get("version", ""))
    if version and not _SEMVER.match(version):
        errors.append(f"version: {version!r} is not semver-shaped (X.Y.Z)")

    spec = str(data.get("requires_hermes", "")).strip()
    if spec:
        for clause in spec.split(","):
            if not _VERSION_SPEC.match(clause.strip()):
                errors.append(f"requires_hermes: {clause!r} is not a parseable version spec")

    for field in ("provides_tools", "provides_hooks", "requires_env"):
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            errors.append(f"{field}: must be a list")
        elif not all(isinstance(v, str) for v in value):
            errors.append(f"{field}: every entry must be a string")

    for field in data:
        if field not in KNOWN_FIELDS:
            warnings.append(f"{field}: not a field Hermes knows about (forward-compat surface)")

    # The declared/registered tool sets must match: Hermes' validator fails
    # catalog admission when they do not.
    try:
        actual = registered_tools()
    except SystemExit:
        return 1

    declared = set(data.get("provides_tools") or [])
    undeclared = actual - declared
    unprovided = declared - actual
    for tool in sorted(undeclared):
        errors.append(f"provides_tools: {tool!r} is registered but not declared")
    for tool in sorted(unprovided):
        errors.append(f"provides_tools: {tool!r} is declared but not registered")

    # The dashboard half, if this plugin has one. Its manifest decides whether
    # `/api/plugins/<id>/` exists at all, and its name is the id the desktop
    # half calls — so a drift here is a door that answers at another address.
    dashboard_errors, dashboard_warnings = check_dashboard_half(name, version)
    errors.extend(dashboard_errors)
    warnings.extend(dashboard_warnings)

    for warning in warnings:
        print(f"WARN  {warning}")
    for error in errors:
        print(f"FAIL  {error}")

    if errors:
        print(f"\n{len(errors)} manifest error(s).")
        return 1

    print(f"OK    plugin.yaml is valid: {name} {version}, "
          f"{len(declared)} tool(s) declared and registered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
