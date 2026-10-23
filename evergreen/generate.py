"""Generating a new template directory from a base template and a recipe.

A generated template is an ordinary template directory: a ``template.yaml``
and a ``cv.tex``, plus whatever the base template carried (its class file,
its fonts, its assets). The existing renderer picks it up with no changes
at all, because nothing about the *render* path changed — only where the
LaTeX came from.

    templates.local/<id>/
    ├── template.yaml      the base's manifest, with a new id and name
    ├── cv.tex             the base's LaTeX, with the settings applied
    ├── recipe.yaml        the base's recipe, re-pointed at the new cv.tex
    ├── settings.yaml      what was chosen, and from which base
    ├── <base>.cls         copied from the base template
    ├── fonts/  fonts.conf copied from the base template
    └── assets/            copied from the base template

The class, fonts, and assets are **copied, not referenced**: the template
contract has no notion of inheritance, and a template directory has to be
self-contained to be portable to another machine.

**The recipe travels with the template.** A generated template's lines are
its own ``cv.tex``, so the recipe that names them belongs to it too: the
base's recipe is written alongside, with its ``id`` rewritten to the new
template and each knob's ``default`` rewritten to the value that was applied.
That makes a generated template configurable again -- re-open it in the
Configurator and it offers the same knobs, starting from the values it was
built with -- and it makes generation compose: generating from a generated
template reaches the same LaTeX as applying both sets of settings to the
base at once.

The rewrite is checked, not assumed. Rewriting one knob can swallow another
knob's pattern (two knobs pointing at the same line is the ordinary way to
get there), and ``apply_settings`` substitutes each pattern in turn, so the
second one would silently match nothing. So the carried recipe is verified
against the generated source before anything is written: every knob must
still match exactly once, or generation fails and leaves no directory
behind.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .career import CareerProfileError, dump_structured, load_structured
from .recipe import (RECIPE_FILENAME, Recipe, RecipeError, apply_settings,
                     check_recipe_against_source, load_recipe, validate_recipe,
                     write_recipe)
from .render import Template, TemplateError, load_template

SETTINGS_FILENAME = "settings.yaml"

# The id a generated template is addressed by. It becomes a directory name
# under ``templates.local/``, so it is validated here rather than trusted
# from the caller: ``hermes career generate --id ../../elsewhere`` — or an
# HTTP request with the same id — must not write outside the workspace.
# Same shape as a recipe's knob ids, which is what a template id already
# looks like in this plugin.
_TEMPLATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


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


def validate_template_id(value: str) -> str:
    """Return the id, or raise if it is not usable as a template id.

    A template id is also a directory name, so this is the one place that
    decides what a caller may create. Lowercase letters, digits, dot, dash
    and underscore, starting with a letter or digit: no separator at the
    front, so an id can never climb out of ``templates.local/``.
    """
    text = str(value or "").strip()
    if not _TEMPLATE_ID_RE.match(text):
        raise GenerateError(
            f"{value!r} is not a usable template id. Use lowercase letters, "
            f"digits, dot, dash or underscore, starting with a letter or "
            f"digit (at most 64 characters)."
        )
    return text


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

    # Before anything is read or created: an unusable id would become an
    # unusable directory name, and refusing here means no caller (CLI, tool,
    # HTTP) can write outside ``templates.local/``.
    template_id = validate_template_id(template_id)

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

    display_name = name or template_id
    carried = _carried_recipe(
        base_dir, recipe, settings, template_id=template_id, name=display_name
    )

    # The carried recipe must still point at real lines in the generated
    # source, or it is a recipe for a template that no longer exists. This is
    # not a formality: rewriting one knob can swallow another knob's pattern,
    # and that would leave a control that silently does nothing. Checked
    # before the directory is created, so a failure writes nothing.
    try:
        errors = validate_recipe(carried)
        if errors:
            raise RecipeError("; ".join(errors[:5]))
        check_recipe_against_source(Recipe(carried), source)
    except RecipeError as exc:
        raise GenerateError(
            f"The settings cannot be carried into {template_id!r}: {exc}"
        ) from exc

    dest_dir.mkdir(parents=True, exist_ok=True)

    main_name = base.main
    (dest_dir / main_name).write_text(source, encoding="utf-8", newline="\n")

    manifest = {field: base.manifest[field] for field in _INHERITED_FIELDS
                if field in base.manifest}
    manifest["id"] = template_id
    manifest["name"] = display_name
    manifest["description"] = description or (
        f"Generated from the {base.name} template."
    )
    dump_structured(manifest, dest_dir / "template.yaml")

    # The recipe the new template's own lines now answer to, so the configurator
    # can re-open a generated template instead of treating it as a dead end.
    try:
        write_recipe(dest_dir, carried, overwrite=True)
    except RecipeError as exc:
        raise GenerateError(str(exc)) from exc

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


def _carried_recipe(
    base_dir: Path,
    recipe: Recipe,
    settings: dict[str, Any],
    *,
    template_id: str,
    name: str,
) -> dict[str, Any]:
    """The base's recipe, re-pointed at the template being generated.

    The base's ``recipe.yaml`` is read back as data rather than rebuilt from
    the ``Recipe`` object, so anything the author wrote -- a description, a
    knob's ``min``/``max``/``options``, an extra field a later format adds --
    survives untouched. Three things change:

    * ``id`` becomes the new template's id, because a recipe describes the
      template it sits in and the generator refuses a mismatch;
    * ``name`` becomes the generated template's display name;
    * every knob that was given a value gets that value as its ``default``,
      in the form it was written into ``cv.tex`` -- a default is the text
      the template already has, and the generated template now has the
      chosen value there.

    A knob nobody touched keeps its own default, which is still exactly what
    its line says.
    """
    path = base_dir / RECIPE_FILENAME
    if not path.exists():
        path = base_dir / "recipe.yml"
    try:
        data = load_structured(path)
    except CareerProfileError as exc:
        raise GenerateError(f"{path.name} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise GenerateError(f"{path.name} must contain a mapping.")

    carried: dict[str, Any] = {
        key: value for key, value in data.items() if key != "knobs"
    }
    carried["id"] = template_id
    carried["name"] = name

    knobs: list[dict[str, Any]] = []
    for spec in data.get("knobs") or []:
        knob = dict(spec)
        knob_id = knob.get("id")
        if knob_id in settings:
            knob["default"] = recipe.knob(knob_id).normalize(settings[knob_id])
        knobs.append(knob)
    carried["knobs"] = knobs
    return carried


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


__all__ = ["GenerateError", "generate_template", "validate_template_id"]
