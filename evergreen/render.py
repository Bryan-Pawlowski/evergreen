"""Template discovery, slot rendering, and .tex assembly.

A template is a directory with a ``template.yaml`` manifest and a ``cv.tex``
holding slot markers::

    %HERMES:SLOT experience
    %HERMES:END

Everything between the markers is replaced by the LaTeX the dialect renders
for that slot. The rest of the file is untouched, so a template author can lay
out slots in any arrangement they like.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from . import dialects
from .career import CareerProfile, SLOTS, load_structured

SLOT_RE = re.compile(
    r"%HERMES:SLOT\s+(?P<name>[A-Za-z0-9_:.-]+)\s*\n(?P<body>.*?)(?:%HERMES:END\s*\n?)",
    re.DOTALL,
)

KNOWN_SLOTS = set(SLOTS) | {"section:<id>"}


class TemplateError(Exception):
    """Raised for a malformed template, manifest, or slot reference."""


class Template:
    """A loaded template: manifest + LaTeX source + dialect."""

    def __init__(self, directory: Path, manifest: dict, source: str):
        self.dir = Path(directory)
        self.manifest = manifest
        self.source = source

    # -- manifest accessors ------------------------------------------------

    @property
    def id(self) -> str:
        return self.manifest.get("id") or self.dir.name

    @property
    def name(self) -> str:
        return self.manifest.get("name") or self.id

    @property
    def description(self) -> str:
        return self.manifest.get("description") or ""

    @property
    def dialect(self) -> str:
        return self.manifest.get("dialect") or "plain"

    @property
    def engine(self) -> str:
        return self.manifest.get("engine") or "lualatex"

    @property
    def main(self) -> str:
        return self.manifest.get("main") or "cv.tex"

    @property
    def columns(self) -> int:
        return int(self.manifest.get("columns") or 1)

    @property
    def page_budget(self) -> int | None:
        value = self.manifest.get("page_budget")
        return int(value) if value is not None else None

    @property
    def class_files(self) -> list[str]:
        return list(self.manifest.get("class_files") or [])

    @property
    def fontconfig(self) -> str | None:
        return self.manifest.get("fontconfig")

    @property
    def fonts_dir(self) -> str | None:
        return self.manifest.get("fonts_dir")

    @property
    def assets_dir(self) -> str | None:
        return self.manifest.get("assets_dir")

    def render_fn(self):
        return dialects.get_dialect(self.dialect, self.dir)


def load_template(directory: Path) -> Template:
    """Load a template from a directory."""
    directory = Path(directory)
    manifest_path = directory / "template.yaml"
    if not manifest_path.exists():
        manifest_path = directory / "template.yml"
    if not manifest_path.exists():
        raise TemplateError(f"No template.yaml in {directory}.")

    manifest = load_structured(manifest_path)
    if not isinstance(manifest, dict):
        raise TemplateError(f"{manifest_path} must contain a mapping.")

    main = manifest.get("main") or "cv.tex"
    source_path = directory / main
    if not source_path.exists():
        raise TemplateError(f"Template {directory.name}: no {main}.")
    source = source_path.read_text(encoding="utf-8")

    return Template(directory, manifest, source)


def discover_templates(*roots: Path) -> list[Template]:
    """Find every template directory under the given roots.

    Later roots win over earlier ones on an id collision, so a workspace's
    ``templates.local/`` overrides a shipped ``templates/<id>/``.
    """
    found: dict[str, Template] = {}
    for root in roots:
        if not root or not Path(root).is_dir():
            continue
        for child in sorted(Path(root).iterdir()):
            if not child.is_dir():
                continue
            if not (child / "template.yaml").exists() and not (child / "template.yml").exists():
                continue
            try:
                template = load_template(child)
            except TemplateError:
                continue
            found[template.id] = template
    return sorted(found.values(), key=lambda t: t.id)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_tex(template: Template, profile: CareerProfile, selection: dict[str, list[dict]] | None = None) -> str:
    """Render the template's LaTeX with the profile's content substituted in.

    ``selection`` maps slot name -> the list of entries to render. It defaults
    to every visible entry of every slot the template uses.
    """
    render = template.render_fn()
    ctx = dialects._mk_ctx(profile, meta={"template": template.id})

    used_slots = set(m.group("name") for m in SLOT_RE.finditer(template.source))

    for name in used_slots:
        if name.startswith("section:"):
            continue
        if name not in SLOTS:
            raise TemplateError(
                f"Template {template.id}: unknown slot {name!r}. "
                f"Known slots: {', '.join(SLOTS)}, section:<id>."
            )

    if selection is None:
        selection = {slot: profile.visible(slot) for slot in used_slots}

    def substitute(match: re.Match[str]) -> str:
        name = match.group("name")
        if name.startswith("section:"):
            items = selection.get(name)
            if items is None:
                items = profile.visible(name)
        else:
            items = selection.get(name) or []
        body = render(name, items, ctx)
        return (body + "\n") if body.strip() else ""

    out = SLOT_RE.sub(substitute, template.source)

    # A slot with no content leaves its marker block removed; verify no
    # markers survived, so a typo never silently renders an empty CV.
    leftover = SLOT_RE.search(out)
    if leftover:
        raise TemplateError(
            f"Template {template.id}: slot {leftover.group('name')!r} was not substituted."
        )
    if "%HERMES:SLOT" in out:
        raise TemplateError(
            f"Template {template.id}: a slot marker survived rendering "
            f"(unbalanced %HERMES:END?)."
        )
    return out


def slots_in(template: Template) -> list[str]:
    """The slot names a template uses, in file order."""
    seen: list[str] = []
    for m in SLOT_RE.finditer(template.source):
        name = m.group("name")
        if name not in seen:
            seen.append(name)
    return seen


# --------------------------------------------------------------------------
# Build staging
# --------------------------------------------------------------------------

def stage(template: Template, dest: Path) -> Path:
    """Copy a template's build inputs into ``dest`` and return the main file.

    The template directory itself is never written to, so a build never
    leaves artifacts in a user's ``templates.local/``.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    shutil.copy2(template.dir / template.main, dest / template.main)

    for cls in template.class_files:
        src = template.dir / cls
        if src.exists():
            shutil.copy2(src, dest / cls)

    if template.fonts_dir:
        src = template.dir / template.fonts_dir
        if src.is_dir():
            target = dest / template.fonts_dir
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)

    if template.assets_dir:
        src = template.dir / template.assets_dir
        if src.is_dir():
            target = dest / template.assets_dir
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)

    if template.fontconfig:
        src = template.dir / template.fontconfig
        if src.exists():
            shutil.copy2(src, dest / template.fontconfig)

    return dest / template.main


__all__ = [
    "Template",
    "TemplateError",
    "discover_templates",
    "load_template",
    "render_tex",
    "slots_in",
    "stage",
]
