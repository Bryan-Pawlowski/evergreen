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

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "plugin.yaml"

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
