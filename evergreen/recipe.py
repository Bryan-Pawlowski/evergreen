"""Recipes: the settings a template exposes, and how to apply them.

A **recipe** is a sibling of ``template.yaml``: ``recipe.yaml`` in the
template's directory. It declares the settings that template offers and,
for each one, the exact LaTeX line the setting rewrites.

    id: plain
    knobs:
      - id: margin
        label: "Page margin"
        type: length
        default: "0.75in"
        latex: "\\usepackage[margin=<<value>>]{geometry}"

The ``latex`` field is a *pattern*, not a literal. ``<<value>>`` is the
placeholder; everything else must appear in the template's ``cv.tex``
exactly as written. A setting is applied by rendering the pattern with
the new value and replacing the line it produced.

**The match count is asserted to be exactly one.** A pattern that matches
nothing means the recipe and the template have drifted apart. A pattern
that matches twice means the rewrite is ambiguous, and guessing which
occurrence was meant is how a template silently renders the wrong thing.
Both are errors, not warnings — the same rule the LaTeX craft skill
applies to hand edits.

Because the pattern must match the template exactly, a base template's
LaTeX is never rewritten to make it configurable: a recipe *points at* the
lines the template already has. The base template renders identically with
or without a recipe, and the shipped templates are unchanged by this
mechanism.

The recipe's ``default`` is the value the template ships with, so it is
also what a form should pre-fill.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .career import CareerProfileError, load_structured

RECIPE_FILENAME = "recipe.yaml"

# The placeholder a knob's LaTeX pattern carries. It is deliberately NOT
# a brace form: LaTeX is full of braces, and a pattern like
# "\titleformat{\section}{<<value>>\bfseries}" must be readable at a
# glance, or a template author cannot tell where the value goes.
PLACEHOLDER = "<<value>>"

# A colour the user picks is "#8F0D0D"; LaTeX's HTML colour model wants
# the bare hex. Both forms are accepted on the way in.
_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")

# A LaTeX length: a number with an optional unit.
_LENGTH_RE = re.compile(r"^\d+(?:\.\d+)?(?:pt|cm|mm|in|em|ex|pc|dd|cc|sp)?$")

# The id a knob or a template is addressed by.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_KNOB_TYPES = ("color", "enum", "number", "length", "text")


class RecipeError(Exception):
    """Raised for a malformed recipe, or settings that do not fit one."""


def _schema_path() -> Path:
    return Path(__file__).resolve().parent.parent / "schema" / "recipe.schema.json"


def validate_recipe(recipe: dict, schema_path: Path | None = None) -> list[str]:
    """Validate a recipe against the JSON schema.

    Returns a list of human-readable error strings (empty = valid). Uses
    ``jsonschema`` when available and falls back to a structural check that
    covers the fields the generator actually depends on.
    """
    spath = schema_path or _schema_path()
    try:
        schema = json.loads(spath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(f"Cannot read the recipe schema at {spath}: {exc}") from exc

    try:
        import jsonschema  # type: ignore
    except ImportError:
        errors = _structural_validate(recipe)
    else:
        validator = jsonschema.Draft202012Validator(schema)
        errors = []
        for err in sorted(validator.iter_errors(recipe), key=lambda e: list(e.path)):
            where = "/".join(str(p) for p in err.path) or "(root)"
            errors.append(f"{where}: {err.message}")

    # The schema can require the placeholder, but not that it appears
    # exactly once, and not that setting ids are unique. Both matter to
    # the generator, so they are checked on every validation path.
    errors.extend(_check_placeholders(recipe))
    return errors


def _check_placeholders(recipe: Any) -> list[str]:
    """Every knob's pattern must carry the placeholder exactly once.

    The JSON schema can require the placeholder's presence, but not its
    count, and not that setting ids are unique. Both matter to the
    generator, so they are checked here on every validation path.
    """
    errors: list[str] = []
    if not isinstance(recipe, dict):
        return errors
    knobs = recipe.get("knobs")
    if not isinstance(knobs, list):
        return errors

    seen: set[str] = set()
    for index, knob in enumerate(knobs):
        if not isinstance(knob, dict):
            continue
        where = f"knobs/{index}"
        kid = knob.get("id")
        if isinstance(kid, str):
            if kid in seen:
                errors.append(f"{where}/id: duplicate setting id {kid!r}")
            seen.add(kid)
        latex = knob.get("latex")
        if isinstance(latex, str) and latex.count(PLACEHOLDER) != 1:
            errors.append(
                f"{where}/latex: must contain {PLACEHOLDER} exactly once, "
                f"found {latex.count(PLACEHOLDER)}"
            )
    return errors


def _structural_validate(recipe: Any) -> list[str]:
    """Minimal validation used when jsonschema is unavailable."""
    errors: list[str] = []
    if not isinstance(recipe, dict):
        return ["recipe must be a mapping"]

    rid = recipe.get("id")
    if not isinstance(rid, str) or not _ID_RE.match(rid):
        errors.append("id: must be a lowercase name starting with a letter or digit")

    knobs = recipe.get("knobs")
    if not isinstance(knobs, list) or not knobs:
        errors.append("knobs: must be a non-empty list")
        return errors

    seen: set[str] = set()
    for index, knob in enumerate(knobs):
        where = f"knobs/{index}"
        if not isinstance(knob, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        kid = knob.get("id")
        if not isinstance(kid, str) or not _ID_RE.match(kid):
            errors.append(f"{where}/id: must be a lowercase name")
        elif kid in seen:
            errors.append(f"{where}/id: duplicate knob id {kid!r}")
        else:
            seen.add(kid)
        if not knob.get("label"):
            errors.append(f"{where}/label: missing")
        ktype = knob.get("type")
        if ktype not in _KNOB_TYPES:
            errors.append(f"{where}/type: must be one of {', '.join(_KNOB_TYPES)}")
        if "default" not in knob:
            errors.append(f"{where}/default: missing")
        latex = knob.get("latex")
        if not isinstance(latex, str) or PLACEHOLDER not in latex:
            errors.append(f"{where}/latex: must contain {PLACEHOLDER}")
        elif latex.count(PLACEHOLDER) != 1:
            errors.append(f"{where}/latex: must contain {PLACEHOLDER} exactly once")
        if ktype == "enum" and not knob.get("options"):
            errors.append(f"{where}/options: an enum knob needs options")
    return errors


class Knob:
    """One setting a template exposes."""

    def __init__(self, spec: dict):
        self.id: str = spec["id"]
        self.label: str = spec.get("label") or spec["id"]
        self.description: str = spec.get("description", "")
        self.type: str = spec.get("type", "text")
        self.default: Any = spec.get("default")
        self.options: list[str] = list(spec.get("options") or [])
        self.min: float | None = spec.get("min")
        self.max: float | None = spec.get("max")
        self.latex: str = spec.get("latex", "")

    # -- value handling ---------------------------------------------------

    def normalize(self, value: Any) -> str:
        """Return the value as it appears in the LaTeX source.

        A colour loses its ``#`` and gains an uppercase hex, because that
        is what ``\\definecolor{..}{HTML}{..}`` wants. Everything else is
        the value as the user gave it.
        """
        if self.type == "color":
            text = str(value).strip()
            match = _HEX_RE.match(text)
            if not match:
                raise RecipeError(
                    f"Setting {self.id!r} wants a hex colour like #8F0D0D, got {value!r}."
                )
            return match.group(1).upper()

        if self.type == "number":
            return _format_number(value)

        return str(value)

    def check(self, value: Any) -> None:
        """Raise if the value is not valid for this knob."""
        if self.type == "enum":
            if self.options and str(value) not in self.options:
                allowed = ", ".join(self.options)
                raise RecipeError(
                    f"Setting {self.id!r} accepts {allowed}; got {value!r}."
                )
            return

        if self.type == "number":
            number = _as_number(value)
            if self.min is not None and number < self.min:
                raise RecipeError(f"Setting {self.id!r} must be at least {self.min}.")
            if self.max is not None and number > self.max:
                raise RecipeError(f"Setting {self.id!r} must be at most {self.max}.")
            return

        if self.type == "length":
            if not _LENGTH_RE.match(str(value).strip()):
                raise RecipeError(
                    f"Setting {self.id!r} wants a LaTeX length like 0.75in, got {value!r}."
                )
            return

        if self.type == "color":
            if not _HEX_RE.match(str(value).strip()):
                raise RecipeError(
                    f"Setting {self.id!r} wants a hex colour like #8F0D0D, got {value!r}."
                )
            return

    def pattern(self) -> str:
        """The LaTeX this knob's default renders to, with the value in place."""
        return self.latex.replace(PLACEHOLDER, self.normalize(self.default))

    def render(self, value: Any) -> str:
        """The LaTeX this knob renders to for the given value."""
        self.check(value)
        return self.latex.replace(PLACEHOLDER, self.normalize(value))

    def as_dict(self) -> dict:
        """The knob as a plain dict, for the API to hand to a form."""
        out = {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "default": self.default,
        }
        if self.description:
            out["description"] = self.description
        if self.options:
            out["options"] = list(self.options)
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        return out


class Recipe:
    """A loaded recipe: the settings a template offers."""

    def __init__(self, data: dict):
        self.id: str = data.get("id", "")
        self.name: str = data.get("name") or self.id
        self.description: str = data.get("description", "")
        self.knobs: list[Knob] = [Knob(spec) for spec in data.get("knobs") or []]

    def knob(self, knob_id: str) -> Knob:
        for knob in self.knobs:
            if knob.id == knob_id:
                return knob
        known = ", ".join(k.id for k in self.knobs)
        raise RecipeError(f"No setting {knob_id!r}. Known settings: {known}.")

    def defaults(self) -> dict[str, Any]:
        """Every setting at its default value."""
        return {knob.id: knob.default for knob in self.knobs}

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "knobs": [knob.as_dict() for knob in self.knobs],
        }


def load_recipe(directory: Path, schema_path: Path | None = None) -> Recipe:
    """Load and validate the recipe in a template directory.

    A directory with no recipe is not an error: a template without a
    recipe simply offers no settings. ``has_recipe`` reports which case
    this is, so a caller can tell "no recipe" from "a broken recipe".
    """
    directory = Path(directory)
    path = directory / RECIPE_FILENAME
    if not path.exists():
        path = directory / "recipe.yml"
    if not path.exists():
        raise RecipeError(f"No {RECIPE_FILENAME} in {directory}.")

    try:
        data = load_structured(path)
    except CareerProfileError as exc:
        raise RecipeError(f"{path.name} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise RecipeError(f"{path.name} must contain a mapping.")

    errors = validate_recipe(data, schema_path)
    if errors:
        detail = "; ".join(errors[:5])
        raise RecipeError(f"{path.name} is not a valid recipe: {detail}")

    return Recipe(data)


def has_recipe(directory: Path) -> bool:
    """Whether a template directory carries a recipe."""
    directory = Path(directory)
    return (directory / RECIPE_FILENAME).exists() or (directory / "recipe.yml").exists()


def check_recipe_against_source(recipe: Recipe, source: str) -> None:
    """Assert every setting in a recipe matches the template exactly once.

    This is the drift check. A recipe that points at a line the template
    does not have (or has twice) cannot be applied safely, and finding
    that out at generation time is much cheaper than finding it out in a
    rendered PDF.
    """
    for knob in recipe.knobs:
        pattern = knob.pattern()
        occurrences = source.count(pattern)
        if occurrences != 1:
            raise RecipeError(
                f"Setting {knob.id!r} matches {occurrences} times in this "
                f"template's source, so it cannot be applied safely. The "
                f"recipe and the template have drifted apart. The line the "
                f"recipe expects is: {pattern}"
            )


def apply_settings(source: str, recipe: Recipe, settings: dict[str, Any]) -> str:
    """Apply a set of settings to a template's LaTeX source.

    Every setting in the recipe is checked against the source first, then
    the chosen values are substituted. The rewrite is a single,
    unambiguous substitution per setting.
    """
    if not isinstance(settings, dict):
        raise RecipeError("Settings must be a mapping of setting id to value.")

    unknown = sorted(set(settings) - {knob.id for knob in recipe.knobs})
    if unknown:
        known = ", ".join(knob.id for knob in recipe.knobs) or "(none)"
        raise RecipeError(
            f"Unknown setting(s): {', '.join(unknown)}. This template offers: {known}."
        )

    check_recipe_against_source(recipe, source)

    out = source
    for knob in recipe.knobs:
        if knob.id not in settings:
            continue
        out = out.replace(knob.pattern(), knob.render(settings[knob.id]), 1)

    return out


def _as_number(value: Any) -> float:
    if isinstance(value, bool):
        raise RecipeError(f"Expected a number, got {value!r}.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RecipeError(f"Expected a number, got {value!r}.") from exc


def _format_number(value: Any) -> str:
    number = _as_number(value)
    if number == int(number):
        return str(int(number))
    return f"{number:g}"


__all__ = [
    "Knob",
    "Recipe",
    "RecipeError",
    "apply_settings",
    "check_recipe_against_source",
    "has_recipe",
    "load_recipe",
    "validate_recipe",
]
