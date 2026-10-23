"""Proposing a recipe from a template.

The recipe generator reads a template and *proposes* a ``recipe.yaml``:
the mechanical candidates filled in, and every candidate that needs a
human decision flagged. It does not write a finished recipe, because
every trap it can find produces a recipe that is syntactically valid
and semantically wrong.

The division of labour (plan §3.2b):

    the generator PROPOSES; the per-knob liveness check DISPOSES.

A candidate is not a setting until a render proves that changing it
changes the page. So the generator's job is to save the human from
transcription, not from judgement.

**Three sources, not one.** A candidate is only a candidate once the
template's ``cv.tex``, its vendored class file, and its dialect have
all been consulted:

* ``cv.tex`` holds the settings, and most of them.
* The **class** may freeze a derived value at load time, so the
  template's own ``\\definecolor`` never reaches the page -- measured on
  ``yaac``, whose template colour was absent from the rendered PDF
  entirely.
* The **dialect** may reference a colour or macro the template never
  mentions, so a generator that read only ``cv.tex`` would discard
  live settings -- measured on ``minimal``, whose ``Paper``, ``Mist``
  and ``GoldenEarth`` are all emitted by the dialect.

**The traps it flags.** Each of these was measured, not guessed:

1. A colour declared but never referenced (the pixels are an image).
2. A derived colour -- a ``\\colorlet`` pointing at another colour.
   Setting one to a hex is a hard LaTeX error, not a style choice.
3. A colour the class freezes at load time -- the template's own
   redefinition of it never reaches the page.
4. A colour only reachable through a macro that is later redefined.
5. A class option -- a value that selects a scheme, not a value.
6. A non-literal value -- a length that contains a macro.
7. An ambiguous line -- one that occurs more than once, so the rewrite
   would be a guess.
8. A value the dialect writes inline, overriding the template's own.
"""

from __future__ import annotations

import re
from typing import Any

from .recipe import PLACEHOLDER

# --- the shapes a candidate can take --------------------------------------

# Each entry: (kind, regex, name-group, value-group, fixed-id). The
# group indices are 1-based over the regex's groups, and the regex must
# match a SUBSTRING of the source, because a recipe's ``latex`` field
# is a pattern that must occur exactly once. A group index of 0 means
# "the whole match", for the patterns with a single group.
_CANDIDATE_PATTERNS = [
    ("color",  re.compile(r"\\definecolor\{([^}]*)\}\{(HTML|RGB|cmyk|HSV)\}\{([0-9A-Fa-f]{6})\}"),
     1, 3, None),
    ("length", re.compile(r"\\setlength\{(\\[A-Za-z]+)\}\{([\d.]+(?:pt|cm|mm|in|em|ex|pc|dd|cc|sp))\}"),
     1, 2, None),
    ("length", re.compile(r"\\newlength\{(\\[A-Za-z]+)\}\{([\d.]+(?:pt|cm|mm|in|em|ex|pc|dd|cc|sp))\}"),
     1, 2, None),
    ("length", re.compile(r"\\newcommand\{\\([A-Za-z]+)\}\{([\d.]+(?:pt|cm|mm|in|em|ex|pc|dd|cc|sp))\}"),
     1, 2, None),
    ("length", re.compile(r"\\setlist\[[^]]*\]\{leftmargin=([^,}]+)"), 0, 1, "bullet_indent"),
    ("length", re.compile(r"\\setlist\[[^]]*\]\{[^}]*?(itemsep|parsep|topsep)=([\d.]+(?:pt|cm|mm|in|em|ex))"),
     1, 2, None),
    ("number", re.compile(r"\\columnratio\{([\d.]+)\}"), 0, 1, "column_ratio"),
    ("size",   re.compile(r"\\documentclass\[([\d]+pt)(?=[,\]])", ), 0, 1, "base_size"),
    ("length", re.compile(r"\\usepackage\[margin=([\d.]+(?:pt|cm|mm|in|em|ex))\]\{geometry\}"),
     0, 1, "margin"),
]

# A \renewcommand of a font macro: the value is a size command plus a
# fixed suffix. The size command is the settable part.
_FONT_RE = re.compile(
    r"\\renewcommand\{\\(namefont|personalinfofont|cvsectionfont|cvsubsectionfont)\}"
    r"\{(\\[A-Za-z]+)(\\rmfamily|\\sffamily)?(\\[A-Za-z]+)?\}"
)

# A \colorlet: a derived colour. Its value is another colour's NAME.
_COLORLET_RE = re.compile(r"\\colorlet\{([^}]*)\}\{([^}]*)\}")

# The colour names a dialect can reference. Read from the dialect's own
# source so it stays in step with the renderer.
_DIALECT_COLOUR_RE = re.compile(r"\\color(?:let)?\{([^}]*)\}")

# The spacing a dialect writes inline -- the values the template's own
# \setlist is overridden by.
_INLINE_SPACING_RE = re.compile(
    r"\\itemsep\s*([\d.]+(?:pt|cm|mm|in|em|ex))"
    r"|\\parsep\s*([\d.]+(?:pt|cm|mm|in|em|ex))"
    r"|\\topsep\s*([\d.]+(?:pt|cm|mm|in|em|ex))"
)

# A macro definition, so a colour referenced only inside a body that is
# later redefined can be spotted.
_MACRO_DEF_RE = re.compile(
    r"\\(?:newcommand|renewcommand)\{\\([A-Za-z]+)\}(?:\[[^]]*\])?\{", re.M
)

# A friendlier name for a LaTeX macro, for the proposed label.
_MACRO_LABELS = {
    "leftcolumn": "Left column width",
    "rightcolumnlength": "Right column width",
    "bandH": "Header band height",
    "washW": "Sidebar wash width",
    "leftcolW": "Sidebar column width",
    "secspace": "Space above a heading",
    "rulegap": "Space above the rule",
    "headgap": "Space below the rule",
    "jobspace": "Space above a job title",
    "titlegap": "Space below a job title",
    "bulletspace": "Space above a bullet list",
    "itemspace": "Space between bullets",
    "parindent": "Paragraph indent",
    "parskip": "Space between paragraphs",
    "emergencystretch": "Emergency stretch",
}

# The size commands a font macro can take, largest first.
_SIZE_COMMANDS = [
    "\\Huge", "\\huge", "\\LARGE", "\\Large", "\\large",
    "\\normalsize", "\\small", "\\footnotesize", "\\scriptsize",
]


# Some candidates are not the kind they look like. A documentclass size
# is a choice among the sizes the class offers, not a free length; and
# a ``leftmargin`` of ``*`` is enumitem's automatic indent, not a
# length at all. The recipe's knob types are the ones a form can
# actually offer, so these are mapped explicitly.
_KNOB_TYPE_OVERRIDES = {
    "base_size": ("enum", ["10pt", "11pt", "12pt"]),
    "bullet_indent": ("enum", ["*", "10pt", "14pt", "18pt", "24pt"]),
}


class Proposal:
    """One candidate setting, and what is wrong with it (if anything)."""

    def __init__(
        self,
        knob_id: str,
        label: str,
        kind: str,
        value: str,
        pattern: str,
        *,
        options: list[str] | None = None,
        traps: list[str] | None = None,
        notes: list[str] | None = None,
        knob_type: str | None = None,
    ):
        self.id = knob_id
        self.label = label
        self.kind = kind
        self.value = value
        self.pattern = pattern
        self.options = options or []
        self.traps = traps or []
        self.notes = notes or []
        # The recipe knob type, which is not always the candidate's kind.
        self.knob_type = knob_type or kind

    @property
    def is_clean(self) -> bool:
        return not self.traps

    def as_knob(self) -> dict[str, Any]:
        """The knob this proposal would become."""
        knob: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "type": self.knob_type,
            "default": self.value,
            "latex": self.pattern.replace(self.value, PLACEHOLDER, 1),
        }
        if self.options:
            # The default must be one of the options, or the knob would
            # reject the template's own value.
            options = list(self.options)
            if self.value not in options:
                options.insert(0, self.value)
            knob["options"] = options
        if self.notes:
            knob["description"] = " ".join(self.notes)
        return knob

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "value": self.value,
            "pattern": self.pattern,
            "options": list(self.options),
            "traps": list(self.traps),
            "notes": list(self.notes),
        }


def _dialect_facts(dialect_source: str) -> tuple[set[str], dict[str, str]]:
    """The colour names and inline spacing one dialect mentions.

    Returns ``(colours, inline_spacing)`` where ``colours`` is every
    colour name the dialect references, and ``inline_spacing`` maps a
    spacing name to the value the dialect writes inline.
    """
    colours = set(_DIALECT_COLOUR_RE.findall(dialect_source))
    inline: dict[str, str] = {}
    for match in _INLINE_SPACING_RE.finditer(dialect_source):
        for name, value in zip(("itemsep", "parsep", "topsep"), match.groups()):
            if name and value:
                inline[name] = value
    return colours, inline


def _strip_comments(source: str) -> str:
    """The source with LaTeX comments removed.

    A commented-out line is not code: the engine never sees it, so a
    setting proposed from one would change nothing. And a comment can
    *mention* a macro -- the workspace's own template explains its
    colour fix in a comment that quotes the very ``\\colorlet`` it is
    about -- so scanning comments invents candidates that do not exist.

    ``\\%`` is an escaped percent, not a comment.
    """
    out: list[str] = []
    for line in source.splitlines(keepends=True):
        cut = len(line)
        index = 0
        while index < len(line):
            if line[index] == "%":
                backslashes = 0
                back = index - 1
                while back >= 0 and line[back] == "\\":
                    backslashes += 1
                    back -= 1
                if backslashes % 2 == 0:
                    cut = index
                    break
            index += 1
        out.append(line[:cut])
    return "".join(out)


def _referenced(name: str, source: str) -> int:
    """How many times a name appears in a source, as a whole word."""
    return len(re.findall(rf"(?<![\w]){re.escape(name)}(?![\w])", source))


def _slug(name: str) -> str:
    """A setting id from a LaTeX macro or colour name."""
    cleaned = name.lstrip("\\").lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")
    return cleaned or "value"


_FIXED_LABELS = {
    "base_size": "Base font size",
    "bullet_indent": "Bullet indent",
    "margin": "Page margin",
    "column_ratio": "Column split",
}


def _label_for(name: str, kind: str, fixed_id: str | None = None) -> str:
    """A human label for a candidate."""
    if fixed_id and fixed_id in _FIXED_LABELS:
        return _FIXED_LABELS[fixed_id]
    bare = name.lstrip("\\")
    known = _MACRO_LABELS.get(bare)
    if known:
        return known
    if kind == "color":
        return f"{bare} colour"
    if kind == "size":
        return "Base font size"
    if kind == "number":
        return "Column split"
    if not bare:
        return "Value"
    return bare.replace("_", " ").capitalize()


def _shadowed_colours(source: str) -> set[str]:
    """Colours reachable only through a macro that is later redefined.

    ``minimal`` defines ``\\faIco`` to use ``IcoCol`` and then
    redefines ``\\faIco`` to use ``RuleGold`` -- so ``IcoCol`` is
    referenced in the source and yet dead. The reference survives only
    in the *first* definition, which the second one discards.

    A macro that is redefined *with the same colour* shadows nothing:
    ``\\jobhead`` is defined twice for the two columns and both
    definitions use ``Ink``, so ``Ink`` is very much alive. So a colour
    is shadowed only when the later definition does not use it either.
    """
    bodies: list[tuple[str, set[str]]] = []
    for match in _MACRO_DEF_RE.finditer(source):
        name = match.group(1)
        depth = 1
        i = match.end()
        while i < len(source) and depth:
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
            i += 1
        body = source[match.end():i]
        bodies.append((name, set(re.findall(r"\\color\{([^}]*)\}", body))))

    by_name: dict[str, list[set[str]]] = {}
    for name, colours in bodies:
        by_name.setdefault(name, []).append(colours)

    shadowed: set[str] = set()
    for colour_sets in by_name.values():
        if len(colour_sets) < 2:
            continue
        # The LAST definition is the one that wins.
        final = colour_sets[-1]
        # A colour mentioned by an earlier definition but not by the
        # final one is unreachable.
        for earlier in colour_sets[:-1]:
            shadowed |= (earlier - final)
    return shadowed


def _class_frozen_colours(class_source: str) -> dict[str, list[str]]:
    """Colours the class derives at load time, and what it derives.

    ``yaac``'s class does ``\\colorlet{linkcolor}{basecolor}`` while it
    is being read, so the class's own value for ``basecolor`` is frozen
    into ``linkcolor`` before the template gets a chance to redefine
    it. The template's hex then reaches nothing.
    """
    frozen: dict[str, list[str]] = {}
    for target, value in _COLORLET_RE.findall(_strip_comments(class_source)):
        frozen.setdefault(value, []).append(target)
    return frozen


def _is_unused_macro(name: str, source: str) -> bool:
    """True when a macro is defined and nothing else refers to it.

    ``minimal`` defines ``\\leftcolW`` and never uses it -- the wash
    panel is sized by ``\\washW`` -- so changing it changes nothing.
    The name is counted across the whole source; one occurrence means
    only its own definition mentions it.
    """
    bare = name.lstrip("\\")
    return _referenced(bare, source) <= 1


def _dialect_overrides(name: str, source: str, dialect_source: str) -> str:
    """The spacing key the dialect writes inline, if it overrides the template.

    ``plain``'s dialect writes ``\\itemsep2pt`` inside
    ``\\begin{itemize}``, which beats the template's own
    ``\\setlist[itemize]``. So the template's ``itemsep`` is dead --
    the value the page shows is the dialect's, not the template's.
    """
    if not dialect_source:
        return ""
    bare = name.lstrip("\\").lower()
    # The dialect's inline \itemsep / \parsep / \topsep.
    if bare not in ("itemsep", "parsep", "topsep"):
        return ""
    if bare not in source.lower():
        return ""
    match = re.search(rf"\\{bare}\s*([\d.]+(?:pt|cm|mm|in|em|ex))", dialect_source)
    return f"\\{bare}{match.group(1)}" if match else ""


def propose(
    source: str,
    template_id: str,
    *,
    dialect_source: str = "",
    class_source: str = "",
) -> list[Proposal]:
    """Propose the settings a template could expose.

    ``source`` is the template's ``cv.tex``; ``dialect_source`` and
    ``class_source`` are the dialect and the vendored class, when they
    are known. The proposals come back in source order.
    """
    dialect_colours, _ = _dialect_facts(dialect_source)
    frozen_by_class = _class_frozen_colours(class_source)
    # Candidates come from the code, not from the prose around it. The
    # ambiguity count still uses the whole source, because a comment
    # that repeats a pattern makes the rewrite ambiguous too.
    scanned = _strip_comments(source)
    shadowed = _shadowed_colours(scanned)
    used_ids: set[str] = set()
    proposals: list[Proposal] = []

    for kind, pattern, name_group, value_group, fixed_id in _CANDIDATE_PATTERNS:
        for match in pattern.finditer(scanned):
            groups = match.groups()
            # A group index of 0 means "the whole match".
            name = groups[name_group - 1] if name_group else ""
            value = groups[value_group - 1] if value_group else match.group(0)
            if value is None:
                continue
            full = match.group(0)
            traps: list[str] = []
            notes: list[str] = []

            # Trap: an ambiguous line. The recipe's match-count rule
            # would reject it, so it is not usable as it stands.
            occurrences = source.count(full)
            if occurrences != 1:
                traps.append(
                    f"this line occurs {occurrences} times; a recipe pattern "
                    f"must match exactly once"
                )

            knob_id = fixed_id or _slug(name)
            if knob_id in used_ids:
                knob_id = f"{knob_id}_2"
            used_ids.add(knob_id)

            if kind == "color":
                in_source = _referenced(name, scanned)
                in_dialect = name in dialect_colours
                derived_from = sorted(
                    target for target, other in _COLORLET_RE.findall(scanned)
                    if other == name
                )
                if derived_from:
                    notes.append("also drives: " + ", ".join(derived_from))
                if name in shadowed:
                    traps.append(
                        "this colour is only reachable through a macro that "
                        "is redefined later, so changing it changes nothing"
                    )
                elif in_source <= 1 and not in_dialect:
                    traps.append(
                        "this colour is declared but never referenced -- the "
                        "pixels it documents are an image, so changing it "
                        "would change nothing"
                    )
                elif in_source <= 1 and in_dialect:
                    notes.append(
                        "referenced by the dialect, not by this template"
                    )
                # Trap: the class freezes this colour at load time.
                if name in frozen_by_class and in_source <= 1:
                    traps.append(
                        "the class derives "
                        + ", ".join(sorted(frozen_by_class[name]))
                        + " from this colour at load time, so redefining it "
                        "here never reaches the page"
                    )

            if kind == "size":
                notes.append("the base size is coupled to the document class")

            if kind == "length" and name:
                # Trap: the name is defined and never used. A macro the
                # template defines but nothing reads is dead -- minimal's
                # \leftcolW is defined and never referenced, because the
                # wash panel is sized by \washW. Only a TEMPLATE-defined
                # macro can be unused: a \setlength sets a LaTeX
                # primitive, which the engine always reads.
                if full.startswith(("\\newcommand", "\\newlength")) and \
                        _is_unused_macro(name, scanned):
                    traps.append(
                        "this macro is defined but never used, so changing "
                        "it changes nothing"
                    )
                # Trap: the dialect writes this key inline, overriding
                # the template's own \setlist. plain's dialect writes
                # \itemsep2pt inside \begin{itemize}, so the template's
                # \setlist[itemize] is dead for that key.
                overridden = _dialect_overrides(name, source, dialect_source)
                if overridden:
                    traps.append(
                        f"the dialect writes {overridden} inline, which "
                        f"overrides the template's own value"
                    )

            proposals.append(Proposal(
                knob_id,
                _label_for(name, kind, fixed_id),
                kind,
                value,
                full,
                traps=traps,
                notes=notes,
                **_knob_type_args(fixed_id, kind, value),
            ))

    proposals.extend(_font_proposals(source, used_ids))
    proposals.sort(key=lambda p: source.find(p.pattern))
    return proposals


def _knob_type_args(fixed_id: str | None, kind: str, value: str) -> dict:
    """The recipe-type arguments for a candidate.

    Most candidates are the kind they look like. The exceptions are
    listed in ``_KNOB_TYPE_OVERRIDES`` -- a documentclass size and a
    ``leftmargin`` of ``*`` are both choices, not free text.
    """
    override = _KNOB_TYPE_OVERRIDES.get(fixed_id or "")
    if override is None:
        return {}
    knob_type, options = override
    if value not in options:
        options = [value] + list(options)
    return {"knob_type": knob_type, "options": options}


def _font_proposals(source: str, used_ids: set[str]) -> list[Proposal]:
    """Proposals for the font macros a template redefines.

    A font macro's value is a size command plus a fixed suffix
    (``\\Huge\\rmfamily\\bfseries``). The size command is the settable
    part, so the proposal is an *enum* of size commands with the suffix
    held fixed -- not a free-text setting.
    """
    proposals = []
    for match in _FONT_RE.finditer(source):
        macro = match.group(1)
        size = match.group(2)
        family = match.group(3) or ""
        bold = match.group(4) or ""
        full = match.group(0)
        suffix = family + bold
        options = [c for c in _SIZE_COMMANDS if c != size]
        knob_id = f"{_slug(macro)}_size"
        if knob_id in used_ids:
            continue
        used_ids.add(knob_id)
        notes = ["the size command only; the rest of the declaration is fixed"]
        if suffix:
            notes.append(f"the fixed suffix is {suffix}")
        proposals.append(Proposal(
            knob_id,
            f"{_slug(macro).replace('_', ' ').capitalize()} size",
            "enum",
            size,
            full,
            options=options,
            notes=notes,
        ))
    return proposals


def propose_recipe(
    source: str,
    template_id: str,
    *,
    dialect_source: str = "",
    class_source: str = "",
) -> dict[str, Any]:
    """The recipe a set of proposals would become.

    Only the CLEAN proposals become knobs. A flagged candidate is
    reported, not written: a knob the generator knows is a trap would
    be a knob that does nothing.

    This is the starting point for a hand-written ``recipe.yaml``, not
    a finished one. Every knob still has to survive the per-knob
    liveness check -- a render that proves changing it changes the
    page -- before it is a setting anyone can rely on.
    """
    proposals = propose(
        source,
        template_id,
        dialect_source=dialect_source,
        class_source=class_source,
    )
    return {
        "id": template_id,
        "knobs": [p.as_knob() for p in proposals if p.is_clean],
    }


def inline_spacing_overrides(source: str, dialect_source: str) -> dict[str, str]:
    """The spacing values the dialect writes inline, overriding the template.

    A template's ``\\setlist`` is dead for any value the dialect also
    sets inline. This names the values so a proposal can say so.
    """
    _, inline = _dialect_facts(dialect_source)
    if not inline:
        return {}
    # Only report the ones the template's own source mentions.
    return {
        name: value for name, value in inline.items()
        if name in source
    }


__all__ = ["Proposal", "inline_spacing_overrides", "propose", "propose_recipe"]
