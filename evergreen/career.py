"""Career profile: loading, validation, and the workspace layout.

The career profile is the structured source of truth for one person's
career data. Markdown notes under ``notes/`` hold prose nuance; this
module owns the facts a CV is rendered from.

The file is named ``career.yaml``, not ``profile.yaml``, on purpose: a
Hermes home contains its own ``profile.yaml`` (the profile descriptor
Hermes writes for its bots), and a plugin that used the generic name
would mistake that file for a career profile.

YAML parsing: the Hermes runtime ships ``ruamel.yaml`` (not PyYAML), so
that is the primary parser. A career profile may also be written as
JSON, which is parsed with the stdlib and needs no dependency at all.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CAREER_FILENAME = "career.yaml"

# The slots a template may request, in the order they appear in a CV.
SLOTS = [
    "identity",
    "summary",
    "skills",
    "experience",
    "projects",
    "education",
    "milestones",
    "links",
    "languages",
]


class CareerProfileError(Exception):
    """Raised when a profile is missing, unreadable, or invalid."""


# --------------------------------------------------------------------------
# YAML / JSON loading
# --------------------------------------------------------------------------

def _load_yaml(text: str) -> Any:
    try:
        from ruamel.yaml import YAML  # type: ignore
    except ImportError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise CareerProfileError(
                "No YAML parser available. Install ruamel.yaml, or write the "
                "profile as JSON (profile.json)."
            ) from exc
        return yaml.safe_load(text)

    loader = YAML(typ="safe")
    return loader.load(text)


def load_structured(path: Path) -> Any:
    """Load a YAML or JSON document from a path."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CareerProfileError(f"Cannot read {path}: {exc}") from exc

    if path.suffix.lower() == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise CareerProfileError(f"{path.name} is not valid JSON: {exc}") from exc

    try:
        return _load_yaml(text)
    except CareerProfileError:
        raise
    except Exception as exc:
        raise CareerProfileError(f"{path.name} is not valid YAML: {exc}") from exc


def dump_structured(data: Any, path: Path) -> None:
    """Write a profile back out, preserving the original format."""
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return

    try:
        from ruamel.yaml import YAML  # type: ignore
    except ImportError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise CareerProfileError("No YAML writer available.") from exc
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return

    dumper = YAML()
    dumper.default_flow_style = False
    dumper.allow_unicode = True
    dumper.width = 100
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        dumper.dump(data, fh)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _schema_path() -> Path:
    return Path(__file__).resolve().parent.parent / "schema" / "career.schema.json"


def validate(profile: dict, schema_path: Path | None = None) -> list[str]:
    """Validate a profile against the JSON schema.

    Returns a list of human-readable error strings (empty = valid). Uses
    ``jsonschema`` when available and falls back to a structural check that
    covers the fields the renderer actually depends on.
    """
    spath = schema_path or _schema_path()
    try:
        schema = json.loads(spath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CareerProfileError(f"Cannot read the profile schema at {spath}: {exc}") from exc

    try:
        import jsonschema  # type: ignore
    except ImportError:
        return _structural_validate(profile)

    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(profile), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{where}: {err.message}")
    return errors


def _structural_validate(profile: Any) -> list[str]:
    """Minimal validation used when jsonschema is unavailable."""
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ["profile must be a mapping"]

    version = profile.get("schema_version")
    if version != 1:
        errors.append(f"schema_version: expected 1, got {version!r}")

    person = profile.get("person")
    if not isinstance(person, dict):
        errors.append("person: missing or not a mapping")
    else:
        name = person.get("name")
        if not isinstance(name, dict) or not name.get("first") or not name.get("last"):
            errors.append("person/name: needs first and last")
        if not person.get("tagline"):
            errors.append("person/tagline: missing")

    for key in ("experience", "projects", "education", "skills", "milestones",
                "languages", "sections"):
        value = profile.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            errors.append(f"{key}: must be a list")
            continue
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                errors.append(f"{key}[{i}]: must be a mapping")
                continue
            if "id" in item and not _valid_id(item["id"]):
                errors.append(f"{key}[{i}].id: {item['id']!r} must be lowercase-hyphen")

    return errors


def _valid_id(value: str) -> bool:
    import re
    return bool(re.match(r"^[a-z0-9][a-z0-9-]*$", value or ""))


def _looks_like_career_profile(path: Path) -> bool:
    """True when a file is recognisably an Evergreen career profile.

    A directory that merely *has* a file of the right name is not a
    workspace. The check is deliberately structural rather than a full
    validation: it must not reject a half-filled profile, only a file
    that is some other document entirely.
    """
    if not path.exists():
        return False
    try:
        data = load_structured(path)
    except ProfileError:
        return False
    except Exception:
        return False
    return (
        isinstance(data, dict)
        and data.get("schema_version") == 1
        and isinstance(data.get("person"), dict)
    )


def _hermes_home_markers(root: Path) -> list[str]:
    """Names that identify a directory as a Hermes home rather than a workspace.

    A Hermes home holds its own ``profile.yaml``, its own config, and its
    own state database. Creating a career workspace inside one would litter
    it with ``notes/``, ``raw/``, and ``out/`` and put a ``career.yaml``
    next to Hermes' own files.
    """
    markers = []
    if (root / "profile.yaml").exists():
        markers.append("profile.yaml")
    if (root / "state.db").exists():
        markers.append("state.db")
    if (root / "hermes_cli").exists():
        markers.append("hermes_cli/")
    if (root / "config.yaml").exists() and (root / "sessions").exists():
        markers.append("config.yaml + sessions/")
    return markers


# --------------------------------------------------------------------------
# CareerProfile access
# --------------------------------------------------------------------------

class CareerProfile:
    """A loaded, validated profile."""

    def __init__(self, data: dict, path: Path | None = None):
        self.data = data
        self.path = path

    # -- accessors ---------------------------------------------------------

    @property
    def person(self) -> dict:
        return self.data.get("person") or {}

    def entries(self, slot: str) -> list[dict]:
        """Return the profile entries for a slot, in profile order."""
        if slot == "identity":
            return [self.person]
        if slot == "summary":
            summary = self.person.get("summary")
            return [{"summary": summary}] if summary else []
        if slot == "links":
            return list(self.person.get("links") or [])
        if slot.startswith("section:"):
            wanted = slot.split(":", 1)[1]
            for sec in self.data.get("sections") or []:
                if sec.get("id") == wanted:
                    return list(sec.get("items") or [])
            return []
        return list(self.data.get(slot) or [])

    def visible(self, slot: str) -> list[dict]:
        """Entries for a slot with ``visible: false`` filtered out."""
        return [e for e in self.entries(slot) if e.get("visible", True)]

    def all_text(self) -> str:
        """Every scrap of prose in the profile, for keyword matching."""
        chunks: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, str):
                chunks.append(value)
            elif isinstance(value, dict):
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)

        walk(self.data)
        return "\n".join(chunks)

    def to_dict(self) -> dict:
        return self.data


# --------------------------------------------------------------------------
# Workspace
# --------------------------------------------------------------------------

class Workspace:
    """A user's career workspace on disk.

    Layout::

        <root>/
        ├── career.yaml         the structured source of truth (the career profile)
        ├── notes/              prose nuance, one markdown file per topic
        ├── raw/                immutable ingested sources
        ├── targets/            per-job-description match results
        ├── out/                rendered PDFs
        ├── templates.local/    user-supplied templates
        └── .evergreen/         workspace metadata
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    # -- paths -------------------------------------------------------------

    @property
    def career_path(self) -> Path:
        for name in ("career.yaml", "career.yml", "career.json"):
            candidate = self.root / name
            if candidate.exists():
                return candidate
        return self.root / "career.yaml"

    @property
    def notes_dir(self) -> Path:
        return self.root / "notes"

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def targets_dir(self) -> Path:
        return self.root / "targets"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def local_templates_dir(self) -> Path:
        return self.root / "templates.local"

    @property
    def meta_dir(self) -> Path:
        return self.root / ".evergreen"

    # -- lifecycle ---------------------------------------------------------

    def exists(self) -> bool:
        """True when this directory holds a *career profile*.

        The file merely existing is not enough: the directory might hold
        an unrelated YAML file with the same name, or a half-written
        profile. What makes a directory a workspace is a career file that
        actually parses as a career profile.
        """
        if not self.root.is_dir():
            return False
        return _looks_like_career_profile(self.career_path)

    def create(self) -> None:
        for d in (self.notes_dir, self.raw_dir, self.targets_dir, self.out_dir,
                  self.local_templates_dir, self.meta_dir):
            d.mkdir(parents=True, exist_ok=True)
        (self.meta_dir / "workspace.json").write_text(
            json.dumps({"schema_version": 1, "created": _today()}, indent=2) + "\n",
            encoding="utf-8",
        )

    def load(self) -> CareerProfile:
        if not self.career_path.exists():
            raise CareerProfileError(
                f"No profile at {self.career_path}. Run `hermes career init` "
                f"or create {self.career_path.name}."
            )
        data = load_structured(self.career_path)
        if not isinstance(data, dict):
            raise CareerProfileError(f"{self.career_path.name} must contain a mapping.")
        errors = validate(data)
        if errors:
            joined = "\n".join(f"  - {e}" for e in errors)
            raise CareerProfileError(f"{self.career_path.name} is invalid:\n{joined}")
        return CareerProfile(data, self.career_path)

    def save(self, profile: CareerProfile) -> None:
        errors = validate(profile.to_dict())
        if errors:
            joined = "\n".join(f"  - {e}" for e in errors)
            raise CareerProfileError(f"Refusing to write an invalid profile:\n{joined}")
        dump_structured(profile.to_dict(), self.career_path)


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def find_workspace(start: Path | None = None) -> Workspace | None:
    """Walk up from ``start`` looking for an existing workspace."""
    current = Path(start or os.getcwd()).resolve()
    for candidate in [current, *current.parents]:
        ws = Workspace(candidate)
        if (candidate / ".evergreen").is_dir() or ws.career_path.exists():
            if ws.exists():
                return ws
    return None


__all__ = [
    "CareerProfile",
    "CareerProfileError",
    "Workspace",
    "SLOTS",
    "dump_structured",
    "find_workspace",
    "load_structured",
    "validate",
]
