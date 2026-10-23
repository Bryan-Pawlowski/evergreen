"""Is a setting real? Prove it with a render.

A setting that is accepted and then changes nothing is the worst
outcome for a form, because the user cannot tell it apart from one
that worked. So a setting is not a setting until a render proves that
changing it, on its own, changes the page.

**Per knob, not on the settings as a whole.** Changing every setting
at once would let a dead knob hide behind a live one: the rendered
page differs, so an aggregate check passes, and the dead knob ships.
Only a per-knob comparison tells the two apart.

**A pixel fingerprint, not text geometry.** A text probe reports font
sizes and left edges. It cannot see a change that only affects an
image overlay -- ``minimal``'s band is a PNG -- so it would report a
false "no change" for a knob that plainly moved something. The
fingerprint is over the rendered pixels of every page, and it is
*identical* when nothing changed, which is what makes it able to prove
a knob is dead.

**More than one profile.** A knob can be dead for the sample profile
and live for real content. ``emergencystretch`` is the example: it
only matters when a line would otherwise overflow, and ordinary
content never makes it overflow. A knob must change the page for
*some* profile, not for every profile.

This module is the "disposes" half of the division of labour recorded
in the plan:

    the generator PROPOSES; the liveness check DISPOSES.

So the authoring path is: propose, prove, write what survived. A
recipe that has been through this module contains only settings that
are known to change the page.
"""

from __future__ import annotations

import copy
import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .build import build_tex, find_tectonic
from .career import CareerProfile
from .generate import generate_template
from .recipe import load_recipe, write_recipe
from .render import load_template, render_tex, stage

# A LaTeX length, with its number and its unit.
_LENGTH_PARTS = re.compile(r"^(\d+(?:\.\d+)?)(pt|cm|mm|in|em|ex|pc|dd|cc|sp)?$")

# The bump each unit gets when the default is zero, since zero times
# anything is still zero.
_ZERO_BUMPS = {
    "pt": "6pt", "cm": "0.4cm", "mm": "4mm", "in": "0.3in",
    "em": "1em", "ex": "1ex", "pc": "1pc", "dd": "6dd",
    "cc": "6cc", "sp": "6sp", "": "6pt",
}

# The stress profile's summary: words long enough that a line would
# otherwise overflow.
_STRESS_SUMMARY = (
    "Supercalifragilisticexpialidociousness "
    "Pneumonoultramicroscopicsilicovolcanoconiosis "
    "Antidisestablishmentarianism "
)


@dataclass
class KnobLiveness:
    """What the render says about one setting."""

    knob_id: str
    live: bool
    value: str | None = None
    profile: str | None = None
    reason: str = ""
    base_fingerprint: str = ""
    changed_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.knob_id,
            "live": self.live,
            "value": self.value,
            "profile": self.profile,
            "reason": self.reason,
        }


@dataclass
class LivenessReport:
    """The result for a whole recipe."""

    template_id: str
    knobs: list[KnobLiveness] = field(default_factory=list)
    skipped: str = ""

    @property
    def live_ids(self) -> list[str]:
        return [k.knob_id for k in self.knobs if k.live]

    @property
    def dead_ids(self) -> list[str]:
        return [k.knob_id for k in self.knobs if not k.live]

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template_id,
            "live": self.live_ids,
            "dead": self.dead_ids,
            "knobs": [k.to_dict() for k in self.knobs],
            "skipped": self.skipped,
        }


def different_value(knob) -> str | None:
    """A second value for a knob, or None if there is no obvious one.

    The alternative is *derived* from the default rather than looked up,
    so a knob nobody anticipated is still tested. A hard-coded table
    would silently skip every new knob, which is the same as not
    testing it.
    """
    if knob.type == "enum" and knob.options:
        for option in knob.options:
            if option != knob.default:
                return option
        return None

    if knob.type == "number":
        try:
            return str(float(knob.default) + 0.1)
        except (TypeError, ValueError):
            return None

    if knob.type == "length":
        return _a_different_length(str(knob.default))

    if knob.type == "color":
        text = str(knob.default).lstrip("#").upper()
        if len(text) == 6:
            # Shift the hex enough to be a different colour, but keep it
            # a colour and not a near-identical one.
            return f"{int(text[:2], 16) ^ 0x40:02X}{text[2:]}"
        return None

    return None


def _a_different_length(default: str) -> str | None:
    """A different length of the same kind.

    The unit is kept, so a setting stays a setting of the same sort --
    a margin does not become a point size, because the two do not mean
    the same thing on the page.
    """
    match = _LENGTH_PARTS.match(default.strip())
    if not match:
        return None
    number, unit = match.group(1), match.group(2) or ""
    try:
        value = float(number)
    except ValueError:
        return None
    if value == 0:
        return _ZERO_BUMPS.get(unit, "6pt")
    # Half again is a visible change without being a different kind of
    # layout.
    bumped = value * 1.5
    text = f"{bumped:.2f}".rstrip("0").rstrip(".")
    return f"{text}{unit}"


def stress_profiles(profile: CareerProfile) -> list[tuple[str, CareerProfile]]:
    """The profiles a knob must change the page for.

    The caller's own profile, plus a stress profile with words long
    enough that a line would otherwise overflow. A setting like
    ``emergencystretch`` only matters when a line is tight, and
    ordinary content never makes it tight -- so a knob that is live
    only under stress reads as dead against the sample alone.
    """
    profiles = [("profile", profile)]
    data = getattr(profile, "data", None)
    if isinstance(data, dict):
        stress = copy.deepcopy(data)
        person = stress.get("person")
        if isinstance(person, dict):
            person["summary"] = _STRESS_SUMMARY
        profiles.append(("stress", CareerProfile(stress)))
    return profiles


def render_fingerprint(pdf: Path, dpi: int = 72) -> str:
    """A fingerprint of what the page actually looks like.

    Returns an empty string when pymupdf is not installed, so the
    caller can skip the check rather than fail it.
    """
    try:
        import pymupdf
    except Exception:
        return ""
    digest = hashlib.sha256()
    # The document is closed explicitly: on Windows an open file cannot
    # be deleted, and the temp tree is cleaned up on the way out.
    doc = pymupdf.open(str(pdf))
    try:
        for page in doc:
            digest.update(page.get_pixmap(dpi=dpi).samples)
    finally:
        doc.close()
    return digest.hexdigest()[:16]


def _compile(template, profile: CareerProfile, build_dir: Path):
    """Render a template with a profile and compile it."""
    tex = render_tex(template, profile)
    main = stage(template, build_dir)
    main.write_text(tex, encoding="utf-8", newline="\n")
    fontconfig = None
    if template.fontconfig and (build_dir / template.fontconfig).exists():
        fontconfig = str((build_dir / template.fontconfig).resolve())
    return build_tex(main, build_dir, engine=template.engine,
                     fontconfig=fontconfig)


def check_knobs(
    template_dir: Path,
    recipe_data: dict,
    profile: CareerProfile,
    *,
    work_dir: Path | None = None,
) -> LivenessReport:
    """Prove, one setting at a time, which of a recipe's settings are real.

    ``template_dir`` is a template directory; ``recipe_data`` is the
    recipe for it -- the whole recipe, with the ``latex`` patterns, as
    a mapping. The template does not have to carry the recipe on disk:
    the check stages a copy with the recipe written into it, which is
    what the authoring path does too (the recipe is being *written*,
    not read).

    Each setting is changed on its own, the result is rendered and
    compiled, and the pages are compared with the base's by pixel
    fingerprint. A setting that leaves the pages identical is dead.

    The check needs Tectonic and pymupdf. Without them it reports
    ``skipped`` rather than claiming the settings are fine.
    """
    template_dir = Path(template_dir)
    report = LivenessReport(template_id=recipe_data.get("id", ""))

    if not find_tectonic():
        report.skipped = "tectonic is not installed"
        return report
    if not _pymupdf_present():
        report.skipped = "pymupdf is not installed"
        return report

    owned = work_dir is None
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="evergreen-liveness-"))
    try:
        # A copy of the template, carrying the recipe. Generating from
        # it is the real path, so what is proved is what a user gets.
        base_dir = work / "base"
        shutil.copytree(template_dir, base_dir)
        write_recipe(base_dir, recipe_data, overwrite=True)
        recipe = load_recipe(base_dir)

        base = load_template(base_dir)
        profiles = stress_profiles(profile)

        base_fingerprints: dict[str, str] = {}
        for label, prof in profiles:
            result = _compile(base, prof, work / f"base_{label}")
            if not result.ok:
                report.skipped = (f"the base template did not compile for the "
                                  f"{label} profile")
                return report
            base_fingerprints[label] = render_fingerprint(result.pdf)

        for knob in recipe.knobs:
            value = different_value(knob)
            if value is None:
                report.knobs.append(KnobLiveness(
                    knob.id, live=False,
                    reason="no second value to test it with",
                ))
                continue

            knob_id = knob.id
            dest = work / f"gen_{knob_id}"
            try:
                generate_template(base_dir, dest, {knob_id: value},
                                  template_id=f"{recipe.id}-{knob_id}")
            except Exception as exc:
                report.knobs.append(KnobLiveness(
                    knob_id, live=False, value=value,
                    reason=f"it does not generate: {exc}",
                ))
                continue

            generated = load_template(dest)
            live = False
            reason = "changing it leaves the page identical"
            tested_profile = None
            changed_fingerprint = ""
            for label, prof in profiles:
                result = _compile(generated, prof, work / f"build_{knob_id}_{label}")
                if not result.ok:
                    reason = (f"it does not compile: "
                              f"{'; '.join(result.errors)[:160]}")
                    continue
                fingerprint = render_fingerprint(result.pdf)
                changed_fingerprint = fingerprint
                if fingerprint != base_fingerprints.get(label, ""):
                    live = True
                    tested_profile = label
                    reason = f"it changes the page under the {label} profile"
                    break

            report.knobs.append(KnobLiveness(
                knob_id, live=live, value=value, profile=tested_profile,
                reason=reason,
                base_fingerprint=base_fingerprints.get(tested_profile or "", ""),
                changed_fingerprint=changed_fingerprint,
            ))
    finally:
        if owned:
            shutil.rmtree(work, ignore_errors=True)

    return report


def _pymupdf_present() -> bool:
    try:
        import pymupdf  # noqa: F401
    except Exception:
        return False
    return True


def keep_live(recipe_data: dict, report: LivenessReport) -> dict:
    """A recipe with the dead settings removed.

    The authoring path is: propose, prove, write what survived. A
    recipe that has been through the liveness check contains only
    settings that are known to change the page.
    """
    dead = set(report.dead_ids)
    if not dead:
        return dict(recipe_data)
    kept = [k for k in recipe_data.get("knobs", []) if k.get("id") not in dead]
    out = dict(recipe_data)
    out["knobs"] = kept
    return out


__all__ = [
    "KnobLiveness",
    "LivenessReport",
    "check_knobs",
    "different_value",
    "keep_live",
    "render_fingerprint",
    "stress_profiles",
]
