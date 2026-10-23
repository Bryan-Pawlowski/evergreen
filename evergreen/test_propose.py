"""The recipe generator: what it proposes, and what it refuses to.

The generator reads a template and proposes the settings it could
expose. Its value is not the proposals -- it is the **traps** it
catches, because every trap produces a recipe that is syntactically
valid and semantically wrong.

So the test is written against the *measured* ground truth from the M1
audit (plan §3.2c), not against the generator's own output. A trap the
audit found and the generator misses is a regression; a live setting the
generator flags is a false alarm, and those are worse: they make the
generator untrustworthy, so a person stops reading its warnings.

Run with the Hermes python (the one that has ruamel.yaml):

    python -m evergreen.test_propose
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import dialects
from .propose import inline_spacing_overrides, propose

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_TEMPLATES = PLUGIN_ROOT / "templates"

# The audit's measured ground truth: for each shipped template, the
# settings that DO change the rendered page, and the ones that do not.
#
# The dead ones are dead for a reason, and the reason is what the
# generator must catch:
#
#   plain   itemsep  -- the plain dialect writes an inline \itemsep2pt,
#                       which beats the template's own \setlist.
#   yaac    basecolor -- the class freezes linkcolor from its OWN
#                       basecolor at load time, so the template's hex
#                       never reaches the page.
#   minimal band, coldark, colwash -- declared but never referenced;
#                       the pixels are a PNG.
#            icocol   -- only reachable through \faIco, which is
#                       redefined later to use RuleGold.
#            leftcolw -- defined and never used; the wash panel is
#                       sized by \washW.
GROUND_TRUTH: dict[str, tuple[set[str], set[str]]] = {
    "plain": (
        {"parindent", "parskip", "base_size", "bullet_indent", "margin"},
        {"itemsep"},
    ),
    "yaac": (
        {"leftcolumn", "rightcolumnlength"},
        {"basecolor"},
    ),
    "altacv": (
        {"slategrey", "lightgrey", "darkpastelred", "pastelred", "goldenearth",
         "column_ratio"},
        set(),
    ),
    "minimal": (
        {"parindent", "bandh", "washw", "secspace", "rulegap", "headgap",
         "jobspace", "titlegap", "bulletspace", "itemspace", "column_ratio",
         "ink", "inksoft", "rulegold", "paper", "mist", "goldenearth",
         "base_size"},
        {"band", "coldark", "colwash", "icocol", "leftcolw"},
    ),
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


def _template_facts(directory: Path) -> tuple[str, str, str]:
    """The template's source, its dialect's source, and its class's source."""
    source = (directory / "cv.tex").read_text(encoding="utf-8")
    manifest = (directory / "template.yaml").read_text(encoding="utf-8")
    dialect = "plain"
    for line in manifest.splitlines():
        if line.startswith("dialect:"):
            dialect = line.split(":", 1)[1].strip()
    class_source = ""
    for class_file in sorted(directory.glob("*.cls")):
        class_source = class_file.read_text(encoding="utf-8", errors="replace")
        break
    return source, dialects.dialect_source(dialect), class_source


def test_matches_the_audit(c: _Checker) -> None:
    """The generator finds exactly the dead settings the audit found.

    Not "at least" -- exactly. A missed trap ships a knob that does
    nothing, and a false alarm makes the generator's warnings noise, so
    a person stops reading them.
    """
    print("\n-- the generator matches the audit --")
    for template_id, (live, dead) in GROUND_TRUTH.items():
        directory = SHIPPED_TEMPLATES / template_id
        if not directory.is_dir():
            c.check(f"propose[{template_id}]: the template exists", False)
            continue
        source, dialect_source, class_source = _template_facts(directory)
        proposals = propose(source, template_id,
                            dialect_source=dialect_source,
                            class_source=class_source)
        flagged = {p.id for p in proposals if not p.is_clean}
        clean = {p.id for p in proposals if p.is_clean}

        missed = dead - flagged
        c.check(f"propose[{template_id}]: every dead setting is flagged",
                not missed, f"missed: {sorted(missed)}")
        false_alarms = flagged - dead
        c.check(f"propose[{template_id}]: no live setting is flagged",
                not false_alarms, f"falsely flagged: {sorted(false_alarms)}")
        # The live settings should at least be among the clean ones.
        absent = live - clean
        c.check(f"propose[{template_id}]: the live settings are proposed",
                not absent, f"not proposed: {sorted(absent)}")


def test_traps_explain_themselves(c: _Checker) -> None:
    """A flagged candidate says WHY it is flagged.

    A trap the reader cannot understand is a trap they will wave
    through, so every flag carries a reason.
    """
    print("\n-- every trap explains itself --")
    for template_id in GROUND_TRUTH:
        directory = SHIPPED_TEMPLATES / template_id
        if not directory.is_dir():
            continue
        source, dialect_source, class_source = _template_facts(directory)
        proposals = propose(source, template_id,
                            dialect_source=dialect_source,
                            class_source=class_source)
        for proposal in proposals:
            if proposal.is_clean:
                continue
            c.check(f"propose[{template_id}/{proposal.id}]: every trap has a reason",
                    all(trap.strip() for trap in proposal.traps))


def test_patterns_are_usable(c: _Checker) -> None:
    """Every proposal's pattern would be a legal recipe pattern.

    A proposal whose pattern does not occur exactly once in the source
    is not a proposal -- the recipe engine rejects it. The generator
    must not offer patterns it knows are unusable.
    """
    print("\n-- every clean proposal is a usable pattern --")
    for template_id in GROUND_TRUTH:
        directory = SHIPPED_TEMPLATES / template_id
        if not directory.is_dir():
            continue
        source, dialect_source, class_source = _template_facts(directory)
        proposals = propose(source, template_id,
                            dialect_source=dialect_source,
                            class_source=class_source)
        for proposal in proposals:
            if not proposal.is_clean:
                continue
            occurrences = source.count(proposal.pattern)
            c.check(
                f"propose[{template_id}/{proposal.id}]: the pattern matches once",
                occurrences == 1,
                f"matched {occurrences} times: {proposal.pattern!r}",
            )


def test_the_dialect_is_scoped(c: _Checker) -> None:
    """One dialect's inline values do not taint another.

    ``plain``'s dialect writes an inline ``\\itemsep``; the other three
    do not. A generator that read the whole dialect module would report
    every template's ``itemsep`` as overridden, which is wrong for three
    of the four.
    """
    print("\n-- the dialect source is scoped to one dialect --")
    for name in ("plain", "yaac", "altacv", "minimal"):
        source = dialects.dialect_source(name)
        c.check(f"propose: {name}'s dialect source is readable", bool(source))
    c.check("propose: only the plain dialect writes an inline itemsep",
            "itemsep" in dialects.dialect_source("plain")
            and "itemsep" not in dialects.dialect_source("minimal")
            and "itemsep" not in dialects.dialect_source("altacv")
            and "itemsep" not in dialects.dialect_source("yaac"))


def test_comments_are_not_code(c: _Checker) -> None:
    """A commented-out line is not a candidate.

    The engine never sees a comment, so a setting proposed from one
    would change nothing. And a comment that *mentions* a macro --
    which is what a comment explaining a fix tends to do -- would
    otherwise invent a candidate, or make a real one look ambiguous.

    The workspace's own template is the case: it explains its colour
    fix in a comment that quotes the very ``\\colorlet`` it is about.
    """
    print("\n-- comments are not code --")
    source = (
        "% \\definecolor{Commented}{HTML}{123456}\n"
        "% \\setlength{\\commented}{9pt}\n"
        "\\definecolor{Real}{HTML}{654321}\n"
        "\\setlength{\\real}{3pt}\n"
    )
    proposals = propose(source, "commented")
    ids = {p.id for p in proposals}
    c.check("propose: a commented-out colour is not proposed",
            "commented" not in ids, str(sorted(ids)))
    c.check("propose: a commented-out length is not proposed",
            "commented_2" not in ids and "commented" not in ids,
            str(sorted(ids)))
    c.check("propose: the live colour is proposed", "real" in ids,
            str(sorted(ids)))
    c.check("propose: the live length is proposed", "real_2" in ids or "real" in ids,
            str(sorted(ids)))

    # A comment that repeats a live pattern makes the rewrite ambiguous,
    # so the candidate is flagged rather than silently rewriting the
    # comment instead of the code.
    shadowed = (
        "% \\definecolor{Real}{HTML}{654321}\n"
        "\\definecolor{Real}{HTML}{654321}\n"
    )
    proposals = propose(shadowed, "shadowed")
    real = [p for p in proposals if p.id == "real"]
    c.check("propose: a pattern repeated in a comment is flagged",
            bool(real) and not real[0].is_clean,
            str([(p.id, p.traps) for p in proposals]))


def test_inline_overrides_are_named(c: _Checker) -> None:
    """The overridden spacing is named, so the warning can be acted on."""
    print("\n-- inline overrides are named --")
    source = (SHIPPED_TEMPLATES / "plain" / "cv.tex").read_text(encoding="utf-8")
    overrides = inline_spacing_overrides(source, dialects.dialect_source("plain"))
    c.check("propose: plain's itemsep override is named",
            "itemsep" in overrides, str(overrides))

    minimal = (SHIPPED_TEMPLATES / "minimal" / "cv.tex").read_text(encoding="utf-8")
    minimal_overrides = inline_spacing_overrides(
        minimal, dialects.dialect_source("minimal"))
    c.check("propose: minimal's own spacing is not overridden by its dialect",
            "itemsep" not in minimal_overrides, str(minimal_overrides))


def main() -> int:
    c = _Checker()
    test_matches_the_audit(c)
    test_traps_explain_themselves(c)
    test_patterns_are_usable(c)
    test_the_dialect_is_scoped(c)
    test_comments_are_not_code(c)
    test_inline_overrides_are_named(c)

    print()
    if c.failures:
        print(f"{c.failures} check(s) FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
