# Evergreen

[![CI](https://github.com/Bryan-Pawlowski/evergreen/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Bryan-Pawlowski/evergreen/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/hermes-%3E%3D0.22.0-6d4aff.svg)](https://github.com/NousResearch/hermes-agent)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776ab.svg)](https://www.python.org/)

A **career workspace and LaTeX CV builder** for Hermes.

Evergreen gives an agent a place to hold one person's career data, and turns
that data into a LaTeX CV — plain, tailored to a job description, or
rendered through any of the shipped templates.

The design principle, inherited from the project this grew out of: **a
rendered CV is a view over the career record, not the source of truth.** What
a CV shows is decided in the career profile, so a change to the CV is a
change to the profile, and the two can never drift apart.

## What it does

- **Intake** — asks for a CV, GitHub repos, and links to work, then keeps
  asking about the skills behind them.
- **A structured career record** — `career.yaml` holds the facts; markdown
  notes hold the nuance; the raw layer holds the original documents,
  immutably.
- **Job-description matching** — a deterministic pass that ranks the career
  profile against a posting and reports the terms *nothing* in it covers.
- **LaTeX CV generation** — renders to PDF with tectonic, and reports the
  page count and the overfull-box count so layout problems are visible
  rather than silent.
- **Your own templates** — drop a directory in `templates.local/` and it
  works. Templates are real LaTeX with a documented slot contract.

## Install

```bash
hermes plugins install <owner>/evergreen
hermes plugins enable evergreen
```

Or from a local checkout:

```bash
hermes plugins install /path/to/evergreen
```

**Requirements:** Python 3.9+ and
[`tectonic`](https://github.com/tectonic-typesetting/tectonic/releases) — a
single static binary, no TeX distribution needed. If tectonic is missing,
`hermes career templates` says so and prints the install steps.

## Use

```
/career init jane              create a named workspace
/career list                   list the workspaces
/career status                 workspace + intake progress
/career templates              list the CV templates
/career match job.txt          match a job description
/career build acme             render the tailored CV
```

Or drive it from the tools: `career_init`, `career_list`, `career_ingest`,
`career_profile`, `career_match`, `career_build`, `career_templates`.

From a shell:

```bash
hermes career list
hermes career status
hermes career build --template yaac --target acme
```

## The workspace

A workspace is created **by name**, and the name becomes a directory
directly under the Hermes home — one level deeper than the home itself:

```
<hermes home>/jane/
├── career.yaml         the career profile — the source of truth
├── notes/              prose nuance, one markdown file per topic
├── raw/                immutable ingested sources (content-hashed)
├── targets/            per-job match reports
├── out/                rendered PDFs and their .tex
├── templates.local/    your own LaTeX templates
└── .evergreen/         intake checklist + workspace metadata
```

The name is recorded in `career.yaml`, so it travels with the data if the
workspace is moved or copied. `/career list` shows the workspaces that
exist, and any tool can be pointed at one with a `name` argument.

The workspace sits alongside Hermes' own state (`plugins/`, `sessions/`,
`skills/`, …), so the plugin refuses a name that collides with something
already in the Hermes home rather than overwriting it.

The rest of it is a plain directory: git-track it, back it up, move it, or
point another tool at it. Nothing about the layout is Hermes-specific. A
workspace is any directory with a `career.yaml` in it — the `notes/`,
`raw/`, and `out/` folders are conventional, not required.

## The career profile

`career.yaml` is the source of truth. It is validated against
`schema/career.schema.json` on every write, so a malformed profile is
rejected with a specific error rather than producing a broken CV.

The file is named `career.yaml` rather than the more obvious
`profile.yaml` on purpose: a Hermes home contains its own `profile.yaml`
(the descriptor Hermes writes for its bots), and a plugin using the generic
name would mistake that file for a career profile.

```yaml
schema_version: 1
person:
  name: {first: Ada, last: Lovelace}
  tagline: "Analytical Engine Programmer"
  summary: "Worked on the engine that turned mathematics into machinery."
  email: "ada@example.com"
  links:
    - {label: "adalovelace", url: "https://github.com/adalovelace", icon: github}
experience:
  - id: engine-room
    role: "Senior Programmer"
    org: "Analytical Engine Project"
    start: "1842"
    end: "1843"
    bullets:
      - text: "Wrote the **first published algorithm** intended for a machine."
    tags: [Algorithms, Mathematics]
    priority: 90
projects:
  - id: notes-g
    title: "Notes on the Analytical Engine"
    date: "1843"
    bullets:
      - text: "Published the first published algorithm. [Scan](https://example.org/g)"
```

**Prose is plain text with a small markup**, never raw LaTeX:

| markup | renders as |
|---|---|
| `**bold**` | bold |
| `_italic_` | italic |
| `` `code` `` | monospace |
| `[label](url)` | a link |

Everything else is escaped for you. This keeps a career profile editable by
someone who does not write LaTeX, portable between templates, and safe —
a profile cannot inject arbitrary macros.

## Templates

| template | layout | engine |
|---|---|---|
| `plain` | one column, plain article | XeTeX |
| `yaac` | one column, keyword table + work longtable | XeTeX |
| `altacv` | two columns with a dashed-rule sidebar | XeTeX |
| `minimal` | two columns, dark info sidebar + header band | XeTeX |

Adding your own: copy `templates/plain/`, edit the LaTeX, keep the slot
markers where you want content to land, drop it in `templates.local/`. The
full contract is in [`templates/README.md`](templates/README.md).

The YaAC and AltaCV classes and the OFL/Apache-licensed fonts are vendored
under `templates/`, so a build is reproducible on any machine with no
system font installation and no TeX distribution.

## Architecture

```
evergreen/
├── plugin.yaml                 manifest
├── __init__.py                 the plugin entry: register(ctx)
├── evergreen/
│   ├── markup.py               inline markup -> LaTeX (the only place it happens)
│   ├── career.py               the career profile: loading, validation, workspace
│   ├── matcher.py              job-description matching
│   ├── dialects.py             per-template-family LaTeX emitters
│   ├── render.py               template discovery + slot substitution
│   ├── build.py                tectonic compile + the build report
│   ├── workspace.py            intake checklist, raw ingest, template listing
│   └── selftest.py             the end-to-end test
├── skills/
│   ├── evergreen-career/       the workflow
│   └── evergreen-cvcraft/      the LaTeX craft
├── schema/career.schema.json
└── templates/
```

**Why a plugin and not just a skill?** The hard parts of a CV build are
deterministic: escaping, LaTeX generation, compiling, measuring the layout.
Leaving those to an LLM re-derives them every session and gets them subtly
wrong — the class macro argument orders alone are enough to break a build.
A plugin puts them in code, where they are tested.

**Why a structured career profile and not just prose?** Tailoring a CV to a job is
a *selection* problem: which of twenty entries, in what order, with what
emphasis. Prose cannot answer that reproducibly. A structured profile can,
which is why the matcher is deterministic and the same posting always
produces the same ranking.

**Why match-then-polish rather than one-shot?** The one-shot approach (the
LLM reads the posting and writes the whole CV) produces a different CV each
time and gives no way to see *why* a line is there. Splitting it means
selection is inspectable and reproducible, and the only step the LLM takes
is rewording bullets that already exist — which is what keeps a tailored CV
honest.

## Development

```bash
python -m evergreen.selftest
```

The selftest exercises the whole pipeline: markup escaping, profile
validation, the matcher, slot rendering, and a real tectonic compile of
every shipped template (asserting the PDF exists, has a page count, and has
no overfull boxes).

## Licence

MIT. The vendored LaTeX classes and fonts keep their own licences — see
`templates/*/LICENSE.md` and the headers in the class files.
