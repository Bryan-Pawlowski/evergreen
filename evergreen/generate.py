"""Generating a new template directory from a base template and a recipe.

A generated template is an ordinary template directory: a ``template.yaml``
and a ``cv.tex``, plus whatever the base template carried (its class file,
its fonts, its assets). The existing renderer picks it up with no changes
at all, because nothing about the *render* path changed — only where the
LaTeX came from.

    templates.local/<id>/
    ├── template.yaml      the base's manifest, with a new id and name
    ├── cv.tex             the base's LaTeX, with the settings applied
    ├── settings.yaml      what was chosen, and from which base
    ├── <base>.cls         copied from the base template
    ├── fonts/  fonts.conf copied from the base template
    └── assets/            copied from the base template

The class, fonts, and assets are **copied, not referenced**: the template
contract has no notion of inheritance, and a template directory has to be
self-contained to be portable to another machine.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .career import dump_structured
from .recipe import RecipeError, apply_settings, load_recipe
from .render import Template, TemplateError, load_template

SETTINGS_FILENAME = "settings.yaml"

# The manifest fields a generated template inherits from its base. The
# rest (id, name, description) are the generated template's own.
_INHERITED_FIELDS = (
    "dialect",
    "engine",
    "main",
    "class_files",
    "fontconfig",
    "fonts_dir",
    "assets_dir",
    "columns",
    "page_budget",
)


class GenerateError(Exception):
    """Raised when a template cannot be generated."""


def generate_template(
    base_dir: Path,
    dest_dir: Path,
    settings: dict[str, Any],
    *,
    template_id: str,
    name: str = "",
    description: str = "",
) -> Path:
    """Write a new template directory from a base template and settings.

    ``base_dir`` is the base template's directory (it must carry a
    ``recipe.yaml``), ``dest_dir`` is the directory to create, and
    ``settings`` maps setting ids to values.
    """
    base_dir = Path(base_dir)
    dest_dir = Path(dest_dir)

    try:
        base = load_template(base_dir)
    except TemplateError as exc:
        raise GenerateError(str(exc)) from exc

    try:
        recipe = load_recipe(base_dir)
    except RecipeError as exc:
        raise GenerateError(str(exc)) from exc

    if recipe.id != base.id:
        raise GenerateError(
            f"The recipe's id ({recipe.id!r}) does not match the template's "
            f"({base.id!r}). They describe the same template."
        )

    try:
        source = apply_settings(base.source, recipe, settings)
    except RecipeError as exc:
        raise GenerateError(str(exc)) from exc

    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise GenerateError(f"{dest_dir} already exists and is not empty.")

    dest_dir.mkdir(parents=True, exist_ok=True)

    main_name = base.main
    (dest_dir / main_name).write_text(source, encoding="utf-8", newline="\n")

    manifest = {field: base.manifest[field] for field in _INHERITED_FIELDS
                if field in base.manifest}
    manifest["id"] = template_id
    manifest["name"] = name or template_id
    manifest["description"] = description or (
        f"Generated from the {base.name} template."
    )
    dump_structured(manifest, dest_dir / "template.yaml")

    # The settings the template was built from, so a generated template
    # records its own provenance and can be regenerated.
    dump_structured(
        {
            "base": base.id,
            "settings": {key: recipe.knob(key).normalize(value)
                         for key, value in settings.items()},
        },
        dest_dir / SETTINGS_FILENAME,
    )

    _copy_build_inputs(base, dest_dir)

    return dest_dir


def _copy_build_inputs(base: Template, dest_dir: Path) -> None:
    """Copy the base template's class file, fonts, and assets."""
    for cls in base.class_files:
        src = base.dir / cls
        if src.exists():
            shutil.copy2(src, dest_dir / cls)

    if base.fonts_dir:
        src = base.dir / base.fonts_dir
        if src.is_dir():
            shutil.copytree(src, dest_dir / base.fonts_dir)

    if base.assets_dir:
        src = base.dir / base.assets_dir
        if src.is_dir():
            shutil.copytree(src, dest_dir / base.assets_dir)

    if base.fontconfig:
        src = base.dir / base.fontconfig
        if src.exists():
            shutil.copy2(src, dest_dir / base.fontconfig)


__all__ = ["GenerateError", "generate_template"]
