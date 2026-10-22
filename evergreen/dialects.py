"""Dialects: how a slot's profile entries become LaTeX.

A dialect is the only part of the template contract that is not plain LaTeX.
It knows the idioms of one template family:

- ``yaac``    the ``yaac-another-awesome-cv`` class
- ``altacv``  the ``altacv`` class
- ``minimal`` a bare ``article`` + ``eso-pic`` layout
- ``plain``   a generic ``article`` renderer (the bootstrap dialect)

Every dialect exposes ``render(slot, items, ctx) -> str`` where ``items`` is
the already-selected list of profile entries for that slot, and ``ctx`` carries
``profile``, ``esc``, ``inline``, and ``meta``.

Text is converted through ``ctx["inline"]`` (the inline markup converter) or
``ctx["esc"]`` (plain escaping). Never write LaTeX by hand around user text.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

from . import markup

Ctx = dict[str, Any]


# Semantic icon names -> LaTeX commands.
#
# The profile stores a *semantic* icon name ("github", "trophy"), not a LaTeX
# command, so a profile is portable between templates that use different
# icon fonts. Each dialect maps the semantic name to whatever its class
# provides; an unmapped name falls back to a neutral bullet.
#
# All three shipped dialects use fontawesome5, so they share this table.
ICONS: dict[str, str] = {
    "github": r"\faGithub",
    "gitlab": r"\faGitlab",
    "linkedin": r"\faLinkedin",
    "email": r"\faEnvelope",
    "phone": r"\faPhone",
    "location": r"\faMapMarker",
    "link": r"\faLink",
    "website": r"\faExternalLink",
    "trophy": r"\faTrophy",
    "cap": r"\faGraduationCap",
    "gamepad": r"\faGamepad",
    "language": r"\faLanguage",
    "star": r"\faStar",
    "tools": r"\faTools",
    "suitcase": r"\faSuitcase",
    "project": r"\faProjectDiagram",
    "education": r"\faMortarBoard",
    "tasks": r"\faTasks",
    "check": r"\faCheck",
    "circle": r"\faCircle",
}


def icon_cmd(name: str | None, fallback: str = r"\faCircle") -> str:
    """Map a semantic icon name to a LaTeX command."""
    if not name:
        return fallback
    return ICONS.get(str(name).lower(), fallback)


def _mk_ctx(profile: Any, meta: dict | None = None) -> Ctx:
    return {
        "career_profile": profile,
        "esc": markup.escape,
        "inline": markup.inline,
        "plain": markup.plain,
        "meta": meta or {},
    }


# --------------------------------------------------------------------------
# plain — the generic bootstrap dialect
# --------------------------------------------------------------------------

def _plain_section(title: str) -> str:
    return "\\section*{%s}" % markup.escape(title)


def plain_render(slot: str, items: list[dict], ctx: Ctx) -> str:
    """Render a slot with only `article` primitives."""
    if not items:
        return ""

    esc = ctx["esc"]
    inline = ctx["inline"]
    out: list[str] = []

    if slot == "identity":
        person = items[0]
        name = person.get("name") or {}
        out.append(r"\begin{center}")
        out.append(r"{\Huge\bfseries %s %s\par}" % (
            esc(name.get("first", "")), esc(name.get("last", ""))))
        if person.get("tagline"):
            out.append(r"{\large %s\par}" % inline(person["tagline"]))
        out.append(r"\end{center}")
        contact = []
        if person.get("email"):
            contact.append(r"\href{mailto:%s}{%s}" % (
                esc(person["email"]), esc(person["email"])))
        if person.get("phone"):
            contact.append(esc(person["phone"]))
        if person.get("location"):
            contact.append(esc(person["location"]))
        if contact:
            out.append(r"\begin{center}")
            out.append(r"{\small " + r" $\cdot$ ".join(contact) + r"\par}")
            out.append(r"\end{center}")

    elif slot == "summary":
        text = items[0].get("summary")
        if text:
            out.append(inline(text))

    elif slot == "links":
        out.append(r"\begin{center}")
        for link in items:
            out.append(r"\href{%s}{%s}" % (esc(link.get("url", "")),
                                            inline(link.get("label", ""))))
        out.append(r"\end{center}")

    elif slot == "experience":
        out.append(_plain_section("Experience"))
        for it in items:
            out.append(_plain_entry(it, ctx))

    elif slot == "projects":
        out.append(_plain_section("Selected Projects"))
        for it in items:
            out.append(_plain_entry(it, ctx))

    elif slot == "education":
        out.append(_plain_section("Education"))
        for it in items:
            out.append(_plain_entry(it, ctx))

    elif slot == "skills":
        out.append(_plain_section("Skills"))
        for it in items:
            label = it.get("label")
            items_list = ", ".join(esc(x) for x in it.get("items") or [])
            if label:
                out.append(r"\textbf{%s}: %s\par" % (esc(label), items_list))
            else:
                out.append(items_list + r"\par")

    elif slot == "milestones":
        out.append(_plain_section("Milestones"))
        for it in items:
            title = inline(it.get("title", ""))
            if it.get("detail"):
                out.append(r"\textbf{%s} --- %s\par" % (title, inline(it["detail"])))
            else:
                out.append(r"\textbf{%s}\par" % title)

    elif slot == "languages":
        out.append(_plain_section("Languages"))
        for it in items:
            name = esc(it.get("name", ""))
            if it.get("level"):
                out.append(r"%s (%s)\par" % (name, esc(it["level"])))
            else:
                out.append(name + r"\par")

    elif slot.startswith("section:"):
        out.append(_plain_section("Section"))
        for it in items:
            out.append(_plain_entry(it, ctx))

    return "\n".join(out)


def _plain_entry(it: dict, ctx: Ctx) -> str:
    """A generic two-line entry: title line, then bullets."""
    esc = ctx["esc"]
    inline = ctx["inline"]
    out: list[str] = []

    title = it.get("role") or it.get("title") or it.get("degree") or ""
    org = it.get("org") or ""
    subtitle = it.get("subtitle") or ""

    if title and org:
        head = r"\textbf{%s} --- %s" % (inline(title), esc(org))
    elif title:
        head = r"\textbf{%s}" % inline(title)
    else:
        head = ""

    dates = _date_range(it)
    if dates:
        # \hfill inside a paragraph does not right-align reliably when the
        # title is long; a zero-width \makebox at the line end does.
        head += r" \hfill {\small %s}\mbox{}" % esc(dates)
    out.append(head + r"\par")

    if subtitle:
        out.append(inline(subtitle) + r"\par")
    if it.get("detail"):
        out.append(inline(it["detail"]) + r"\par")
    if it.get("summary"):
        out.append(inline(it["summary"]) + r"\par")

    bullets = it.get("bullets") or []
    if bullets:
        out.append(r"\begin{itemize}\itemsep2pt \parsep0pt \topsep3pt")
        for b in bullets:
            text = inline(b.get("text", ""))
            if b.get("url"):
                text += r" \href{%s}{%s}" % (esc(b["url"]), esc(b["url"]))
            out.append(r"\item " + text)
        out.append(r"\end{itemize}")

    if it.get("tags"):
        tags = ", ".join(esc(t) for t in it["tags"])
        out.append(r"{\small %s\par}" % tags)

    out.append(r"\vspace{4pt}")
    return "\n".join(out)


def _date_range(it: dict) -> str:
    start = it.get("start")
    end = it.get("end")
    if start and end:
        return "%s -- %s" % (start, end)
    return start or end or ""


# --------------------------------------------------------------------------
# yaac — the yaac-another-awesome-cv class
# --------------------------------------------------------------------------

def yaac_render(slot: str, items: list[dict], ctx: Ctx) -> str:
    """Render for the ``yaac-another-awesome-cv`` class.

    The class's macros and their argument orders, verified against the
    vendored ``.cls``:

    - ``\\experience{end}{title}{org}{country}{start}{body}{tech-list}``
      — note the **end date comes first** and the tech list is a
      ``\\foreach``-style comma list, not a LaTeX list.
    - ``\\project{name}{dates}{links}{description}{tech-list}``
    - ``\\scholarshipentry{date}{description}`` inside ``scholarship``
    - ``\\keywordsentry{label}{description}`` inside ``keywords``
    - ``\\sectionTitle{title}{icon}``
    """
    if not items:
        return ""
    esc = ctx["esc"]
    inline = ctx["inline"]
    out: list[str] = []

    if slot == "identity":
        person = items[0]
        # The class's header is built from \name / \tagline / \socialinfo, and
        # \makecvheader is called by the TEMPLATE after this slot. So the
        # identity slot emits the declarations, not the rendered header.
        name = person.get("name") or {}
        out.append(r"\name{%s}{%s}" % (
            esc(name.get("first", "")), esc(name.get("last", ""))))
        if person.get("tagline"):
            out.append(r"\tagline{%s}" % inline(person["tagline"]))
        contact = []
        if person.get("email"):
            contact.append(r"\email{%s}" % esc(person["email"]))
        if person.get("phone"):
            contact.append(r"\smartphone{%s}" % esc(person["phone"]))
        if person.get("location"):
            contact.append(r"\address{%s}" % esc(person["location"]))
        for link in person.get("links") or []:
            label = link.get("label", "")
            icon = link.get("icon")
            if icon == "github":
                contact.append(r"\github{%s}" % esc(label))
            elif icon == "gitlab":
                contact.append(r"\gitlab{%s}" % esc(label))
            elif icon == "linkedin":
                contact.append(r"\linkedin{%s}" % esc(label))
            elif icon == "website":
                contact.append(r"\website{%s}{%s}" % (
                    esc(link.get("url", "")), esc(label)))
        if contact:
            out.append(r"\socialinfo{" + " \\\\\n  ".join(contact) + "}")

    elif slot == "summary":
        text = items[0].get("summary")
        if text:
            out.append(inline(text))

    elif slot == "skills":
        out.append(r"\sectionTitle{Competences}{\faTasks}")
        out.append(r"\begin{keywordstable}")
        for it in items:
            label = esc(it.get("label", ""))
            desc = inline(", ".join(it.get("items") or []))
            out.append(r"\keywordsentry{%s}{%s}" % (label, desc))
        out.append(r"\end{keywordstable}")

    elif slot == "experience":
        out.append(r"\sectionTitle{Experience}{\faSuitcase}")
        out.append(r"\begin{experiences}")
        for it in items:
            out.append(_yaac_experience(it, ctx))
        out.append(r"\end{experiences}")

    elif slot == "projects":
        out.append(r"\sectionTitle{Selected Projects}{\faPlus}")
        out.append(r"\begin{projects}")
        for it in items:
            out.append(_yaac_project(it, ctx))
        out.append(r"\end{projects}")

    elif slot == "education":
        out.append(r"\sectionTitle{Education}{\faMortarBoard}")
        out.append(r"\begin{scholarship}")
        for it in items:
            out.append(_yaac_education(it, ctx))
        out.append(r"\end{scholarship}")

    elif slot == "milestones":
        out.append(r"\sectionTitle{Career Milestones}{\faTrophy}")
        out.append(r"\begin{keywordstable}")
        for it in items:
            date = it.get("date") or ""
            detail = inline(it.get("detail", "")) if it.get("detail") else ""
            body = inline(it.get("title", ""))
            if detail:
                body += ": " + detail
            out.append(r"\keywordsentry{%s}{%s}" % (esc(date), body))
        out.append(r"\end{keywordstable}")

    elif slot == "languages":
        out.append(r"\sectionTitle{Languages}{\faLanguage}")
        out.append(r"\begin{keywordstable}")
        for it in items:
            label = esc(it.get("name", ""))
            if it.get("level"):
                label += ", " + esc(it["level"])
            out.append(r"\keywordsentry{%s}{}" % label)
        out.append(r"\end{keywordstable}")

    elif slot.startswith("section:"):
        out.append(r"\sectionTitle{%s}{\faStar}" % esc(_section_title(ctx, slot)))
        out.append(r"\begin{projects}")
        for it in items:
            out.append(_yaac_project(it, ctx))
        out.append(r"\end{projects}")

    return "\n".join(out)


def _yaac_experience(it: dict, ctx: Ctx) -> str:
    """``\\experience{end}{title}{org}{country}{start}{body}{tags}``.

    The class renders the tech list with ``\\foreach \\n in {#7}{\\cvtag{\\n}}``,
    so it is a plain comma-separated list of names.
    """
    esc = ctx["esc"]
    inline = ctx["inline"]
    body: list[str] = []
    if it.get("summary"):
        body.append(inline(it["summary"]))
    bullets = it.get("bullets") or []
    if bullets:
        body.append(r"\begin{itemize}")
        for b in bullets:
            text = inline(b.get("text", ""))
            if b.get("url"):
                text += r" \href{%s}{%s}" % (esc(b["url"]), esc(b["url"]))
            body.append(r"\item " + text)
        body.append(r"\end{itemize}")
    tags = ", ".join(esc(t) for t in it.get("tags") or [])
    return r"\experience{%s}{%s}{%s}{%s}{%s}{%s}{%s}" % (
        esc(it.get("end") or ""),
        esc(it.get("role") or ""),
        esc(it.get("org") or ""),
        esc(it.get("location") or ""),
        esc(it.get("start") or ""),
        "".join(body),
        tags,
    )


def _yaac_project(it: dict, ctx: Ctx) -> str:
    """``\\project{name}{dates}{links}{description}{tech-list}``."""
    esc = ctx["esc"]
    inline = ctx["inline"]
    links = ""
    if it.get("url"):
        links = r"\website{%s}{%s}" % (esc(it["url"]), esc(it["url"]))
    body = inline(it.get("subtitle", "")) if it.get("subtitle") else ""
    bullets = it.get("bullets") or []
    if bullets:
        body += r"\\" + " ".join(
            inline(b.get("text", "")) for b in bullets
        )
    return r"\project{%s}{%s}{%s}{%s}{%s}" % (
        esc(it.get("title") or ""),
        esc(it.get("date") or ""),
        links,
        body,
        ", ".join(esc(t) for t in it.get("tags") or []),
    )


def _yaac_education(it: dict, ctx: Ctx) -> str:
    """``\\scholarshipentry{date}{description}``."""
    esc = ctx["esc"]
    inline = ctx["inline"]
    description = "%s, %s" % (esc(it.get("degree") or ""), esc(it.get("org") or ""))
    if it.get("detail"):
        description += ". " + inline(it["detail"])
    return r"\scholarshipentry{%s}{%s}" % (
        esc(_date_range(it)),
        description,
    )


# --------------------------------------------------------------------------
# altacv — the altacv class
# --------------------------------------------------------------------------

def altacv_render(slot: str, items: list[dict], ctx: Ctx) -> str:
    if not items:
        return ""
    esc = ctx["esc"]
    inline = ctx["inline"]
    out: list[str] = []

    if slot == "identity":
        person = items[0]
        # \name takes ONE argument (the full name); \personalinfo holds the
        # contact lines; \makecvheader is called by the TEMPLATE after this
        # slot.
        name = person.get("name") or {}
        full_name = ("%s %s" % (name.get("first", ""), name.get("last", ""))).strip()
        out.append(r"\name{%s}" % esc(full_name))
        if person.get("tagline"):
            out.append(r"\tagline{%s}" % inline(person["tagline"]))
        contact = []
        if person.get("email"):
            contact.append(r"\email{%s}" % esc(person["email"]))
        if person.get("phone"):
            contact.append(r"\phone{%s}" % esc(person["phone"]))
        if person.get("location"):
            contact.append(r"\location{%s}" % esc(person["location"]))
        for link in person.get("links") or []:
            label = link.get("label", "")
            icon = link.get("icon")
            if icon == "github":
                contact.append(r"\github{%s}" % esc(label))
            elif icon == "gitlab":
                contact.append(r"\gitlab{%s}" % esc(label))
            elif icon == "linkedin":
                contact.append(r"\linkedin{%s}" % esc(label))
            elif icon == "homepage":
                contact.append(r"\homepage{%s}" % esc(label))
        if contact:
            out.append(r"\personalinfo{" + "\n  ".join(contact) + "}")

    elif slot == "summary":
        text = items[0].get("summary")
        if text:
            out.append(inline(text))

    elif slot == "skills":
        out.append(r"\cvsection{Skills}")
        for it in items:
            if it.get("label"):
                out.append(r"\cvsubsection{%s}" % inline(it["label"]))
            skills = it.get("items") or []
            for i, skill in enumerate(skills):
                tag = r"\cvtag{%s}" % esc(skill)
                # \cvtag is a tikz node; consecutive tags need an explicit
                # line break or they run together on one line. The last tag
                # closes the paragraph so the next \cvsection (whose
                # \nointerlineskip) is not in horizontal mode.
                if i < len(skills) - 1:
                    out.append(tag + r"\\[4pt]")
                else:
                    out.append(tag + r"\par")
            out.append(r"\medskip")

    elif slot == "experience":
        out.append(r"\cvsection{Experience}")
        for it in items:
            out.append(_altacv_event(it, ctx))

    elif slot == "projects":
        out.append(r"\cvsection{Selected Projects}")
        for it in items:
            out.append(_altacv_event(it, ctx))

    elif slot == "education":
        out.append(r"\cvsection{Education}")
        for it in items:
            out.append(_altacv_event(it, ctx))

    elif slot == "milestones":
        out.append(r"\cvsection{Career Milestones}")
        for it in items:
            out.append(r"\cvachievement{%s}{%s}{%s}" % (
                icon_cmd(it.get("icon"), r"\faTrophy"), inline(it.get("title", "")),
                inline(it.get("detail", "")) if it.get("detail") else ""))
        # \cvachievement is a tabularx; it leaves the paragraph open, so
        # close it before the next \cvsection.
        out.append(r"\par")

    elif slot == "languages":
        out.append(r"\cvsection{Languages}")
        for it in items:
            name = esc(it.get("name", ""))
            if it.get("level"):
                out.append(r"%s (%s)" % (name, esc(it["level"])))
            else:
                out.append(name)
        out.append(r"\par")

    return "\n".join(out)


def _altacv_event(it: dict, ctx: Ctx) -> str:
    """``\\cvevent{title}{org}{date}{location}`` plus the body.

    ``\\cvevent`` ends in horizontal mode, and ``\\divider`` does not close
    the paragraph, so a ``\\par`` is required before the next ``\\cvsection``
    — whose ``\\nointerlineskip`` fails in horizontal mode.
    """
    esc = ctx["esc"]
    inline = ctx["inline"]
    title = it.get("role") or it.get("title") or it.get("degree") or ""
    org = it.get("org") or ""
    subtitle = it.get("subtitle") or ""
    dates = _date_range(it)
    location = it.get("location") or ""

    # A project has no org; its subtitle plays that role in the org slot.
    if not org and subtitle:
        org = subtitle

    out = [r"\cvevent{%s}{%s}{%s}{%s}" % (
        inline(title), esc(org), esc(dates), esc(location))]

    if it.get("summary"):
        out.append(inline(it["summary"]))
    bullets = it.get("bullets") or []
    if bullets:
        out.append(r"\begin{itemize}")
        for b in bullets:
            text = inline(b.get("text", ""))
            if b.get("url"):
                text += r" \href{%s}{%s}" % (esc(b["url"]), esc(b["url"]))
            out.append(r"\item " + text)
        out.append(r"\end{itemize}")
    if it.get("detail"):
        out.append(inline(it["detail"]))
    out.append(r"\divider\par")
    return "\n".join(out)


# --------------------------------------------------------------------------
# minimal — the bare article + eso-pic layout
# --------------------------------------------------------------------------

def minimal_render(slot: str, items: list[dict], ctx: Ctx) -> str:
    if not items:
        return ""
    esc = ctx["esc"]
    inline = ctx["inline"]
    out: list[str] = []

    if slot == "identity":
        person = items[0]
        # The header band: name, tagline, summary. The band is a fixed-height
        # box, so a summary that grows past three lines will need \bandH
        # re-derived.
        name = person.get("name") or {}
        out.append(r"{\color{Paper}\Huge\rmfamily\bfseries %s %s\par}" % (
            esc(name.get("first", "")), esc(name.get("last", ""))))
        if person.get("tagline"):
            out.append(r"{\color{GoldenEarth}\large %s\par}" % inline(person["tagline"]))
        if person.get("summary"):
            out.append(r"{\color{Mist}\small %s\par}" % inline(person["summary"]))
        contact = []
        if person.get("email"):
            contact.append(r"\href{mailto:%s}{%s}" % (
                esc(person["email"]), esc(person["email"])))
        if person.get("phone"):
            contact.append(esc(person["phone"]))
        if person.get("location"):
            contact.append(esc(person["location"]))
        if contact:
            out.append(r"{\color{Mist}\small %s\par}" % r" $\cdot$ ".join(contact))

    elif slot == "summary":
        text = items[0].get("summary")
        if text:
            out.append(inline(text))

    elif slot == "skills":
        out.append(r"\sechead{Skills}")
        for it in items:
            if it.get("label"):
                out.append(r"{\color{InkSoft}\itshape %s\par\nobreak}" % inline(it["label"]))
            for skill in it.get("items") or []:
                out.append(r"\lchip{%s}" % inline(skill))
            out.append(r"\vspace{5pt}")

    elif slot == "experience":
        out.append(r"\secheadfirst{Work Experience}")
        for it in items:
            out.append(_minimal_entry(it, ctx))

    elif slot == "projects":
        out.append(r"\sechead{Selected Projects}")
        for it in items:
            out.append(_minimal_entry(it, ctx))

    elif slot == "education":
        out.append(r"\sechead{Education}")
        for it in items:
            out.append(_minimal_entry(it, ctx))

    elif slot == "milestones":
        out.append(r"\sechead{Career Milestones}")
        for it in items:
            detail = inline(it.get("detail", "")) if it.get("detail") else ""
            date = it.get("date")
            head = r"\faIco{%s}\ { \color{Ink}\rmfamily\bfseries %s }" % (
                icon_cmd(it.get("icon"), r"\faTrophy"), inline(it.get("title", "")))
            if date:
                head += r"\ $ \cdot $ \ {\color{InkSoft}\small %s}" % esc(date)
            out.append(head)
            if detail:
                out.append(r"\par\nobreak{\color{InkSoft}\small %s}\par\nobreak\smallskip" % detail)
            else:
                out.append(r"\par\nobreak\smallskip")

    elif slot == "links":
        out.append(r"\sechead{Links}")
        for link in items:
            out.append(r"\faIco{%s} \href{%s}{%s}\par" % (
                icon_cmd(link.get("icon"), r"\faLink"),
                esc(link.get("url", "")), inline(link.get("label", ""))))

    elif slot == "languages":
        out.append(r"\sechead{Languages}")
        for it in items:
            out.append(r"\faIco{%s} %s\par" % (
                icon_cmd("language", r"\faLanguage"), esc(it.get("name", ""))))

    return "\n".join(out)


def _minimal_entry(it: dict, ctx: Ctx) -> str:
    esc = ctx["esc"]
    inline = ctx["inline"]
    out: list[str] = []

    title = it.get("role") or it.get("title") or it.get("degree") or ""
    org = it.get("org") or ""
    head = inline(title)
    if org:
        head += ", " + esc(org)
    if it.get("subtitle"):
        head += r" (\textit{%s})" % inline(it["subtitle"])

    out.append(r"\jobhead{%s}" % head)

    dates = _date_range(it)
    location = it.get("location") or ""
    if dates and location:
        # The separator is LaTeX, so it must not go through esc() — only the
        # two text parts are escaped.
        out.append(r"\jobdates{%s \ $ \cdot $ \ %s}" % (esc(dates), esc(location)))
    elif dates:
        out.append(r"\jobdates{%s}" % esc(dates))

    if it.get("summary"):
        out.append(inline(it["summary"]))
    if it.get("detail"):
        out.append(r"{\color{InkSoft} %s\par}" % inline(it["detail"]))

    bullets = it.get("bullets") or []
    if bullets:
        out.append(r"\begin{jobitems}")
        for b in bullets:
            text = inline(b.get("text", ""))
            if b.get("url"):
                text += r" \href{%s}{%s}" % (esc(b["url"]), esc(b["url"]))
            out.append(r"\item " + text)
        out.append(r"\end{jobitems}")

    return "\n".join(out)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

_BUILTIN: dict[str, Callable[[str, list[dict], Ctx], str]] = {
    "plain": plain_render,
    "yaac": yaac_render,
    "altacv": altacv_render,
    "minimal": minimal_render,
}


def _section_title(ctx: Ctx, slot: str) -> str:
    """Look up the title of a user-defined section from the profile."""
    wanted = slot.split(":", 1)[1]
    profile = ctx.get("career_profile")
    data = getattr(profile, "data", profile) or {}
    for sec in data.get("sections") or []:
        if sec.get("id") == wanted:
            return sec.get("title") or wanted
    return wanted


def get_dialect(name: str, template_dir: Path | None = None):
    """Return the render function for a dialect name.

    ``custom`` loads ``dialect.py`` from the template directory.
    """
    if name in _BUILTIN:
        return _BUILTIN[name]

    if name == "custom":
        if template_dir is None:
            raise ValueError("A custom dialect needs a template directory.")
        module_path = Path(template_dir) / "dialect.py"
        if not module_path.exists():
            raise ValueError(f"No dialect.py in {template_dir}.")
        spec = importlib.util.spec_from_file_location("evergreen_dialect", module_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load dialect module at {module_path}.")
        module = importlib.util.module_from_spec(spec)
        sys.modules["evergreen_dialect"] = module
        spec.loader.exec_module(module)
        render = getattr(module, "render", None)
        if not callable(render):
            raise ValueError(f"{module_path} does not define a callable render().")
        return render

    raise ValueError(
        f"Unknown dialect {name!r}. Known dialects: {', '.join(sorted(_BUILTIN))}, custom."
    )


def known_dialects() -> list[str]:
    return sorted(_BUILTIN) + ["custom"]


__all__ = ["Ctx", "get_dialect", "known_dialects", "plain_render", "yaac_render",
           "altacv_render", "minimal_render", "_mk_ctx"]
