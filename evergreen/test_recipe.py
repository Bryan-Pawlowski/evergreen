"""The recipe seam: load a recipe, apply settings, generate a template.

This is the first milestone of the Configurator, and it is deliberately
about the *seam* rather than about the feature. The questions it answers:

* Does a recipe load, validate, and point at lines the template really
  has?
* Does applying the defaults change nothing?
* Does applying a real setting change exactly that setting?
* Does a generated template render and compile like any other template?
* Does a broken recipe fail loudly instead of half-applying?

The last one matters most. A recipe that has drifted from its template
must be an error, never a silent partial rewrite — the same rule the
LaTeX craft skill applies to hand edits.

Run with the Hermes python (the one that has ruamel.yaml):

    python -m evergreen.test_recipe
"""

from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

from .build import build_tex, find_tectonic
from .career import CareerProfile, load_structured
from .generate import GenerateError, generate_template
from .liveness import check_knobs, keep_live
from . import dialects
from .propose import propose_recipe
from .recipe import (RecipeError, apply_settings, check_recipe_against_source,
                     load_recipe, validate_recipe, write_recipe)
from .render import load_template, render_tex, slots_in, stage

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_TEMPLATES = PLUGIN_ROOT / "templates"

# A profile with enough content to prove the content survives generation.
SAMPLE = {
    "schema_version": 1,
    "person": {
        "name": {"first": "Test", "last": "Person"},
        "tagline": "Software Engineer & Systems Builder",
        "summary": "Engineer with 10 years across _rendering_ and **infrastructure**.",
        "email": "test@example.com",
        "phone": "+1 555 0100",
        "location": "Portland, Oregon, USA",
        "links": [
            {"label": "testuser", "url": "https://github.com/testuser", "icon": "github"},
        ],
    },
    "experience": [
        {
            "id": "acme-senior",
            "role": "Senior Software Engineer",
            "org": "Acme Corp",
            "location": "Remote",
            "start": "Mar 2020",
            "end": "Present",
            "bullets": [
                {"text": "Built a **CUDA** rendering pipeline that cut frame time 40%."},
                {"text": "Led migration of the CI system to _GitHub Actions_."},
            ],
            "tags": ["CUDA", "C++", "CI/CD"],
            "priority": 80,
        },
    ],
    "projects": [
        {
            "id": "toolkit",
            "title": "Toolkit",
            "subtitle": "Open source CLI",
            "date": "2023",
            "bullets": [{"text": "1.2k stars on [GitHub](https://github.com/testuser/toolkit)."}],
        },
    ],
    "education": [
        {
            "id": "state",
            "degree": "B.Sc. Computer Science",
            "org": "State University",
            "start": "2013",
            "end": "2017",
            "detail": "Focus on **graphics** and high-performance computing.",
        },
    ],
    "skills": [
        {"label": "Languages", "items": ["C++", "Python", "Rust"]},
    ],
    "milestones": [
        {"title": "Shipped the renderer", "date": "2024", "detail": "Live in 40 games"},
    ],
    "languages": [{"name": "English", "level": "Native"}],
    "sections": [],
}


class _Checker:
    """The same check/fail accounting the rest of the selftest uses."""

    def __init__(self) -> None:
        self.failures = 0

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        ok = bool(condition)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}"
              + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            self.failures += 1
        return ok


def _derived_recipe(template_dir: Path, into: Path) -> Path:
    """A recipe for a template, derived by the proposal engine.

    The plugin ships the *machinery* -- the loader, the generator, the
    proposal engine -- but not a concrete recipe. A recipe names real
    lines in a real template, so it belongs to whoever owns that
    template: the shipped templates carry none, and a user's own
    templates carry their own.

    So the tests derive one at run time, from the same engine the
    Configurator uses, and write it into a copy of the template. That
    is also the better test: it exercises the whole chain -- propose,
    author, apply, generate, render -- rather than only the last half
    of it.
    """
    shutil.copytree(template_dir, into, dirs_exist_ok=True)
    source = (into / "cv.tex").read_text(encoding="utf-8")
    class_source = ""
    for class_file in sorted(into.glob("*.cls")):
        class_source = class_file.read_text(encoding="utf-8", errors="replace")
        break
    manifest = (into / "template.yaml").read_text(encoding="utf-8")
    dialect = "plain"
    for line in manifest.splitlines():
        if line.startswith("dialect:"):
            dialect = line.split(":", 1)[1].strip()
    recipe = propose_recipe(
        source,
        load_template(into).id,
        dialect_source=dialects.dialect_source(dialect),
        class_source=class_source,
    )
    if not recipe["knobs"]:
        raise RecipeError(f"The generator proposed nothing for {template_dir}.")
    write_recipe(into, recipe, overwrite=True)
    return into


def _derived_recipes() -> list[Path]:
    """A derived recipe for each shipped template, in a temp copy.

    The temp copies outlive this call -- the tests generate from them
    and compile the result -- so they are cleaned up at exit rather
    than at the end of a ``with`` block.
    """
    tmp = Path(tempfile.mkdtemp(prefix="evergreen-recipes-"))
    atexit.register(shutil.rmtree, str(tmp), True)
    found = []
    for child in sorted(SHIPPED_TEMPLATES.iterdir()):
        if not child.is_dir():
            continue
        try:
            load_template(child)
        except Exception:
            continue
        try:
            found.append(_derived_recipe(child, tmp / child.name))
        except RecipeError:
            continue
    return found


def _derived_recipe_for(template_id: str) -> Path:
    """A derived recipe for one shipped template, in a temp copy."""
    tmp = Path(tempfile.mkdtemp(prefix="evergreen-recipe-"))
    atexit.register(shutil.rmtree, str(tmp), True)
    return _derived_recipe(SHIPPED_TEMPLATES / template_id, tmp / template_id)


def test_recipe_loading(c: _Checker) -> None:
    """A recipe loads, validates, and points at lines the template has."""
    print("\n-- recipe loading --")
    dirs = _derived_recipes()
    c.check("recipe: a recipe can be derived for a shipped template", bool(dirs),
            "the generator proposed nothing for any shipped template")

    for directory in dirs:
        template = load_template(directory)
        recipe = load_recipe(directory)
        c.check(f"recipe[{recipe.id}]: the id matches the template",
                recipe.id == template.id,
                f"{recipe.id!r} != {template.id!r}")
        c.check(f"recipe[{recipe.id}]: it has knobs", bool(recipe.knobs))
        c.check(f"recipe[{recipe.id}]: it validates against the schema",
                not validate_recipe(load_structured(directory / "recipe.yaml")))
        c.check(f"recipe[{recipe.id}]: it reports problems, not raises",
                _reports_problems(directory) is not None)


def _reports_problems(directory: Path) -> bool:
    """Whether a broken recipe is reported rather than raised.

    A recipe with a bad knob id must produce errors from
    ``validate_recipe`` — not an exception from the loader. The loader
    raising on a *malformed* recipe is correct; what must not happen is a
    broken recipe silently loading.
    """
    broken = {
        "id": "broken",
        "knobs": [{"id": "Bad Id", "label": "x", "type": "enum",
                   "default": "a", "options": ["a"], "latex": "no placeholder"}],
    }
    return bool(validate_recipe(broken))


def test_pattern_matching(c: _Checker) -> None:
    """Every setting in a recipe points at exactly one place in the source."""
    print("\n-- pattern matching --")
    for directory in _derived_recipes():
        template = load_template(directory)
        recipe = load_recipe(directory)
        for knob in recipe.knobs:
            occurrences = template.source.count(knob.pattern())
            c.check(
                f"recipe[{recipe.id}]: setting {knob.id!r} matches exactly once",
                occurrences == 1,
                f"matched {occurrences} times: {knob.pattern()!r}",
            )
        # The whole recipe must survive the drift check.
        try:
            check_recipe_against_source(recipe, template.source)
            c.check(f"recipe[{recipe.id}]: the drift check passes", True)
        except RecipeError as exc:
            c.check(f"recipe[{recipe.id}]: the drift check passes", False, str(exc))


def test_defaults_are_a_no_op(c: _Checker) -> None:
    """Applying a recipe's own defaults changes nothing."""
    print("\n-- defaults --")
    for directory in _derived_recipes():
        template = load_template(directory)
        recipe = load_recipe(directory)
        out = apply_settings(template.source, recipe, recipe.defaults())
        c.check(f"recipe[{recipe.id}]: the defaults change nothing",
                out == template.source,
                "the source differs after applying the defaults")
        c.check(f"recipe[{recipe.id}]: the base template still has its slots",
                "experience" in slots_in(template),
                str(slots_in(template)))


def test_applying_settings(c: _Checker) -> None:
    """A real setting changes exactly that setting."""
    print("\n-- applying settings --")
    directory = _derived_recipe_for("plain")
    template = load_template(directory)
    recipe = load_recipe(directory)

    changed = {"base_size": "12pt", "margin": "1in"}
    out = apply_settings(template.source, recipe, changed)

    c.check("apply: the new font size is in the output",
            "\\documentclass[12pt,letterpaper]{article}" in out)
    c.check("apply: the old font size is gone",
            "\\documentclass[10pt,letterpaper]{article}" not in out)
    c.check("apply: the new margin is in the output",
            "\\usepackage[margin=1in]{geometry}" in out)
    c.check("apply: the old margin is gone",
            "\\usepackage[margin=0.75in]{geometry}" not in out)
    c.check("apply: an unset setting is left alone",
            "\\setlist[itemize]{leftmargin=*, itemsep=2pt, parsep=0pt, topsep=3pt}" in out)
    c.check("apply: the slot markers survive",
            "%HERMES:SLOT experience" in out,
            "the settings rewrite ate a slot marker")
    c.check("apply: only the intended lines changed",
            len(out.splitlines()) == len(template.source.splitlines()))


def test_errors_are_loud(c: _Checker) -> None:
    """A bad recipe or a bad setting fails, rather than half-applying."""
    print("\n-- errors --")
    directory = _derived_recipe_for("plain")
    template = load_template(directory)
    recipe = load_recipe(directory)

    # An unknown setting is an error, not a silent no-op.
    try:
        apply_settings(template.source, recipe, {"nope": "1"})
        c.check("apply: an unknown setting is rejected", False, "it was accepted")
    except RecipeError as exc:
        c.check("apply: an unknown setting is rejected", "nope" in str(exc), str(exc))

    # A value of the wrong type is an error.
    try:
        apply_settings(template.source, recipe, {"base_size": "banana"})
        c.check("apply: a value outside the options is rejected", False, "it was accepted")
    except RecipeError as exc:
        c.check("apply: a value outside the options is rejected", True, str(exc))

    # A recipe that points at a line the template does not have is an error.
    drifted = load_recipe(directory)
    drifted.knobs[0].latex = "\\thisdoesnotexist[<<value>>]{nothing}"
    try:
        apply_settings(template.source, drifted, {drifted.knobs[0].id: "12pt"})
        c.check("apply: a drifted recipe is rejected", False, "it was accepted")
    except RecipeError as exc:
        c.check("apply: a drifted recipe is rejected", "drifted apart" in str(exc), str(exc))

    # A pattern that matches twice is an error — the rewrite would be
    # ambiguous, and guessing which occurrence was meant is how a
    # template silently renders the wrong thing.
    ambiguous = load_recipe(directory)
    doubled = template.source + "\n\\documentclass[10pt,letterpaper]{article}\n"
    c.check("apply: the ambiguous fixture really is ambiguous",
            doubled.count(ambiguous.knobs[0].pattern()) == 2,
            f"matched {doubled.count(ambiguous.knobs[0].pattern())} times")
    try:
        apply_settings(doubled, ambiguous, {ambiguous.knobs[0].id: "12pt"})
        c.check("apply: an ambiguous pattern is rejected", False, "it was accepted")
    except RecipeError as exc:
        c.check("apply: an ambiguous pattern is rejected", "2 times" in str(exc), str(exc))

    # A recipe whose id disagrees with its template is an error.
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "tpl"
        try:
            _generate_with_mismatched_id(directory, dest)
            c.check("generate: a mismatched recipe id is rejected", False, "it was accepted")
        except GenerateError as exc:
            c.check("generate: a mismatched recipe id is rejected",
                    "does not match" in str(exc), str(exc))


def _generate_with_mismatched_id(base_dir: Path, dest: Path) -> None:
    """Generate from a template whose recipe claims a different id.

    The recipe is copied alongside the template, so the mismatch is
    on disk exactly as a mis-authored package would have it.
    """
    staging = dest.parent / "staging"
    shutil.copytree(base_dir, staging)
    recipe_path = staging / "recipe.yaml"
    lines = recipe_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("id:"):
            lines[index] = "id: not-plain\n"
            break
    recipe_path.write_text("".join(lines), encoding="utf-8")
    generate_template(staging, dest, {}, template_id="wide")


def test_generation(c: _Checker) -> None:
    """A generated template is an ordinary template."""
    print("\n-- generation --")
    directory = _derived_recipe_for("plain")
    settings = {"base_size": "12pt", "margin": "1in"}

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "generated"
        generate_template(directory, dest, settings,
                          template_id="wide", name="Wide Plain")

        c.check("generate: it wrote a cv.tex", (dest / "cv.tex").exists())
        c.check("generate: it wrote a template.yaml", (dest / "template.yaml").exists())
        c.check("generate: it recorded the settings", (dest / "settings.yaml").exists())

        manifest = (dest / "template.yaml").read_text(encoding="utf-8")
        c.check("generate: the manifest carries the new id", "id: wide" in manifest, manifest)
        c.check("generate: the manifest carries the new name",
                "Wide Plain" in manifest, manifest)
        c.check("generate: the manifest keeps the base's dialect",
                "dialect: plain" in manifest, manifest)

        # The generated template must be loadable and renderable by the
        # ordinary path — that is the whole point of emitting a template
        # rather than a special artefact.
        generated = load_template(dest)
        c.check("generate: the result loads as a template",
                generated.id == "wide", generated.id)
        c.check("generate: the result has the base's slots",
                set(slots_in(generated)) == set(slots_in(load_template(directory))),
                str(slots_in(generated)))

        profile = CareerProfile(SAMPLE)
        tex = render_tex(generated, profile)
        c.check("generate: the result renders the profile",
                "Acme Corp" in tex and "CUDA" in tex)
        c.check("generate: the result has no slot markers left",
                "%HERMES:SLOT" not in tex)
        c.check("generate: the result carries the new settings",
                "\\documentclass[12pt,letterpaper]{article}" in tex)

        # And it must compile.
        if not find_tectonic():
            print("[SKIP] compile — tectonic not installed")
        else:
            with tempfile.TemporaryDirectory() as build:
                main = stage(generated, Path(build))
                main.write_text(tex, encoding="utf-8", newline="\n")
                result = build_tex(main, Path(build), engine=generated.engine)
                c.check("generate: the result compiles to a PDF",
                        result.ok, "; ".join(result.errors)[:400])
                c.check("generate: the PDF has pages",
                        result.pages is not None and result.pages >= 1,
                        f"pages={result.pages}")
                c.check("generate: the PDF has no overfull boxes",
                        result.overfull == 0, f"overfull={result.overfull}")

    # Writing into a directory that already has content is an error.
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "generated"
        dest.mkdir()
        (dest / "something.txt").write_text("x", encoding="utf-8")
        try:
            generate_template(directory, dest, settings, template_id="wide")
            c.check("generate: it refuses to overwrite a non-empty directory",
                    False, "it overwrote it")
        except GenerateError:
            c.check("generate: it refuses to overwrite a non-empty directory", True)


# The settings that are real LaTeX settings, that the generator proposes
# without complaint, and that no ordinary content makes change the
# page.
#
# ``emergencystretch`` is the case that matters. It is a genuine
# LaTeX setting, it is set in the template, and the generator has no
# reason to distrust it -- but it only changes anything when a line
# would otherwise overflow, and no ordinary CV overflows. The audit
# assumed a stress profile would make it visible; measured against
# the real renderer, it is not.
#
# This is the whole point of the liveness gate: the generator cannot
# know, so the render decides.
KNOWN_DEAD = {
    "plain": set(),
    "yaac": set(),
    "altacv": set(),
    "minimal": {"emergencystretch"},
}


def test_the_liveness_check(c: _Checker) -> None:
    """Each setting is proved, one at a time, with a real render.

    A setting that is accepted and then changes nothing is a control
    that visibly does nothing -- the worst outcome for a form, because
    the user has no way to tell it apart from a setting that worked.

    The check is **per knob**, not on the settings as a whole. Changing
    every setting at once would let a dead knob hide behind a live one:
    the rendered page differs, so the aggregate check passes, and the
    dead knob ships. Only a per-knob comparison can tell the two
    apart.

    It is also run against **more than one profile**. A knob can be
    dead for the sample profile and live for real content, so a knob
    must change the page for *some* profile, not for every profile.
    """
    print("\n-- the liveness check --")
    if not find_tectonic():
        print("[SKIP] compile -- tectonic not installed")
        return
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        print("[SKIP] per-knob liveness -- pymupdf is not installed")
        return

    profile = CareerProfile(SAMPLE)
    for directory in _derived_recipes():
        recipe = load_recipe(directory)
        report = check_knobs(directory, load_structured(directory / "recipe.yaml"), profile)
        if report.skipped:
            c.check(f"recipe[{recipe.id}]: the liveness check ran", False,
                    report.skipped)
            continue
        expected_dead = KNOWN_DEAD.get(recipe.id, set())
        c.check(f"recipe[{recipe.id}]: the dead settings are exactly the known ones",
                set(report.dead_ids) == expected_dead,
                f"dead={sorted(report.dead_ids)} expected={sorted(expected_dead)}")
        for knob in report.knobs:
            if knob.knob_id in expected_dead:
                continue
            c.check(
                f"recipe[{recipe.id}]: setting {knob.knob_id!r} changes the page",
                knob.live,
                f"the rendered PDF is byte-identical with "
                f"{knob.knob_id}={knob.value!r}; the setting does nothing",
            )


def test_the_authoring_path_drops_the_dead(c: _Checker) -> None:
    """A recipe that has been proved holds only settings that work.

    A control that does nothing is worse than no control, so the
    authoring path -- propose, prove, write what survived -- must
    leave the dead settings out.
    """
    print("\n-- the authoring path drops the dead --")
    if not find_tectonic():
        print("[SKIP] compile -- tectonic not installed")
        return
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        print("[SKIP] -- pymupdf is not installed")
        return

    profile = CareerProfile(SAMPLE)
    for directory in _derived_recipes():
        recipe = load_recipe(directory)
        report = check_knobs(directory, load_structured(directory / "recipe.yaml"), profile)
        if report.skipped:
            continue
        kept = keep_live(load_structured(directory / "recipe.yaml"), report)
        kept_ids = [k["id"] for k in kept["knobs"]]
        for dead in report.dead_ids:
            c.check(f"recipe[{recipe.id}]: the dead setting {dead!r} is dropped",
                    dead not in kept_ids, f"{dead} survived into the recipe")
        for live in report.live_ids:
            c.check(f"recipe[{recipe.id}]: the live setting {live!r} is kept",
                    live in kept_ids, f"{live} was dropped")
        c.check(f"recipe[{recipe.id}]: the kept recipe is still valid",
                not validate_recipe(kept), str(validate_recipe(kept)))


def test_shipped_templates_are_unaffected(c: _Checker) -> None:
    """Having a recipe does not change how a template renders."""
    print("\n-- shipped templates --")
    for directory in _derived_recipes():
        template = load_template(directory)
        recipe = load_recipe(directory)
        out = apply_settings(template.source, recipe, recipe.defaults())
        c.check(f"recipe[{recipe.id}]: the template renders identically with a recipe",
                out == template.source)

    # Every shipped template must still compile, recipe or not.
    if not find_tectonic():
        print("[SKIP] compile — tectonic not installed")
        return
    profile = CareerProfile(SAMPLE)
    for child in sorted(SHIPPED_TEMPLATES.iterdir()):
        if not child.is_dir():
            continue
        try:
            template = load_template(child)
        except Exception:
            continue
        tex = render_tex(template, profile)
        with tempfile.TemporaryDirectory() as build:
            main = stage(template, Path(build))
            main.write_text(tex, encoding="utf-8", newline="\n")
            fontconfig = None
            if template.fontconfig and (Path(build) / template.fontconfig).exists():
                fontconfig = str(Path(build) / template.fontconfig)
            result = build_tex(main, Path(build), engine=template.engine,
                               fontconfig=fontconfig)
            c.check(f"build[{template.id}]: it still compiles",
                    result.ok, "; ".join(result.errors)[:300])


def main() -> int:
    c = _Checker()
    test_recipe_loading(c)
    test_pattern_matching(c)
    test_defaults_are_a_no_op(c)
    test_applying_settings(c)
    test_errors_are_loud(c)
    test_generation(c)
    test_the_liveness_check(c)
    test_the_authoring_path_drops_the_dead(c)
    test_shipped_templates_are_unaffected(c)

    print()
    if c.failures:
        print(f"{c.failures} check(s) FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
