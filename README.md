# Evergreen

[![CI](https://github.com/Bryan-Pawlowski/evergreen/actions/workflows/ci.yml/badge.svg?branch=stable)](https://github.com/Bryan-Pawlowski/evergreen/actions/workflows/ci.yml)
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
hermes plugins install Bryan-Pawlowski/evergreen
hermes plugins enable evergreen
```

That checks out `stable`, the repository's default branch — the release
channel. Every commit there passed its CI checks (leak, lint, render
parity, negative tests) before it was pushed, and the repository
protection keeps force-push and history deletion off the branch.

To pin a specific revision instead, use its commit SHA:

```bash
hermes plugins install Bryan-Pawlowski/evergreen --ref d168975dc8f93820ef02de6f677dce158f88be5f
```

Or from a local checkout:

```bash
hermes plugins install /path/to/evergreen
```

### The test channel

`main` is the integration branch, and both branches carry the same four
required checks, so a `main` tip has passed CI. What it has not been through is
the install-and-drive pass a promotion gets, which is where the failures CI
cannot see show up: a dependency missing on the plugin host, a backend still
serving pre-install code. Run it in a throwaway profile, not the one you build
CVs with:

```bash
hermes profile create evergreen-test        # once
SHA=$(git ls-remote https://github.com/Bryan-Pawlowski/evergreen.git refs/heads/main | cut -f1)
hermes -p evergreen-test plugins install Bryan-Pawlowski/evergreen --ref "$SHA" --force
```

`--ref` takes a full 40-character commit and nothing else, so a branch name or
a tag is refused and the SHA has to be resolved first. A pinned install is
immutable by design: `hermes plugins update` refuses it and prints the SHA it
is held at along with the reinstall command, so moving to a newer `main` means
running those last two lines again. An install into your active profile
replaces the release copy there, so re-pin that profile to `stable` when you
have finished testing.

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

## The Configurator (desktop pane)

The Hermes desktop app can show the Configurator as a pane docked on the right:
pick a base template, set the knobs its `recipe.yaml` exposes, watch a schematic
preview follow the values, generate a template directory in `templates.local/`,
and build the PDF — with the real page count and overfull-box count coming back
from tectonic.

It is the same feature the CLI and the HTTP door expose (`hermes career recipe`,
`author`, `generate`, and `/api/plugins/evergreen/`), so none of them can drift
from the others: the form is generated from the recipe the backend returns, and
every request goes to the plugin's own routes.

**It ships switched off.** A plugin's desktop half runs inside the app window, so
it is opt-in like every other:

1. Put the plugin where the app can see it — `~/.hermes/plugins/evergreen/`,
   where `hermes plugins install` puts it.
2. In the app, open **Capabilities → Plugins** and turn **Evergreen
   Configurator** on. It inventories there even while off.
3. Open it from the command palette (⌘K / Ctrl+K) → **Configurator**, or the
   pane's own tab.

**Choosing a workspace.** The pane lists the career workspaces under the Hermes
home plus the one `plugins.entries.evergreen.settings.workspace_path` names. A
workspace anywhere else — including the hand-maintained tree this repo was built
alongside — is addressed by its **path**, in the field below the list. The
backend refuses to guess a workspace from the server's working directory, so a
path is the only honest way to name one.

**Generating needs a recipe.** A base template is configurable only when it
carries a `recipe.yaml`, and the shipped templates deliberately carry none. Author
one with `hermes career author <template> --path <template dir>`: it proposes,
proves each setting with a render, and writes only what survived.

Proving a setting compares the rendered **pixels**, so authoring needs
**PyMuPDF** (`python -m pip install pymupdf`). Without it, `hermes career author`
still writes the recipe but reports `"proven": false` with
`"why": "pymupdf is not installed"` and keeps every proposal — honest about it,
but you get the unproven set instead of the settings that actually change the
page.

**A generated template is configurable too.** The base's recipe travels into the
new directory, re-pointed at the new `cv.tex`, with each knob starting from the
value it was built with — so `wide` can be re-opened as a base and tweaked again,
and generating from a generated template reaches the same LaTeX as applying both
sets of settings at once.

**Working on the pane.** The app loads a *copy* at
`~/.hermes/desktop-plugins/evergreen/plugin.js`, refreshed by Rescan, an
install/update, or a restart — editing `plugins/evergreen/desktop/plugin.js` in
place does nothing on screen. Link a checkout instead
(`ln -s <checkout> ~/.hermes/plugins/evergreen`) and every save re-syncs and
hot-reloads it.

The gate is `python scripts/check_desktop_plugin.py`: the SDK-surface lint (a
port of the one inside `hermes plugins validate`, which CI cannot run), the
package contract, a syntax check — and the check that keeps the two halves
honest, that **every `ctx.rest()` path the pane calls is a route the backend
serves**. `python scripts/test_desktop_lint_teeth.py` proves each of those rules
can fail.

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
│   ├── recipe.py               a template's settings, and how they are applied
│   ├── generate.py             settings -> a new template directory
│   ├── propose.py              the recipe generator: propose, and flag the traps
│   ├── liveness.py             does changing one setting change the page?
│   ├── selftest.py             the end-to-end test
│   └── test_*.py               the recipe seam, the generator, the HTTP door
├── dashboard/                  the HTTP door, mounted at /api/plugins/evergreen/
│   ├── manifest.json
│   ├── plugin_api.py
│   └── dist/index.js           no-op SPA entry (there is no browser tab)
├── desktop/
│   └── plugin.js               the Configurator pane (opt-in; desktop app only)
├── skills/
│   ├── evergreen-career/       the workflow
│   └── evergreen-cvcraft/      the LaTeX craft
├── schema/                     career, recipe, settings
├── scripts/                    the CI gates
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
git config core.hooksPath .githooks   # once, per clone: the local gate
python -m evergreen.selftest
```

The selftest exercises the whole pipeline: markup escaping, profile
validation, the matcher, slot rendering, and a real tectonic compile of
every shipped template (asserting the PDF exists, has a page count, and has
no overfull boxes).

### The PDF text-layer checks

Two checks go deeper than the selftest: they read the compiled PDF's *text
layer* to prove the content actually reached the page.

```bash
python scripts/check_render_parity.py          # every shipped template
python scripts/check_generated_parity.py       # a generated template
python scripts/test_render_parity_teeth.py     # prove the check can fail
```

These need a **PDF text extractor**, which is not part of the plugin's
runtime requirements — the plugin itself never reads a PDF back. Install
either one:

```bash
pip install pymupdf          # preferred; pure wheel, no system install
```

or poppler's `pdftotext` (`apt install poppler-utils`, `brew install
poppler`, `choco install poppler`).

Without one, the checks exit **2** and say so, rather than reporting every
template as missing all of its content. A missing dependency is an
environment problem, not a broken template.

**Branch model.** `main` is the development branch; `stable` is the release
channel (the repository's default branch, which is what
`hermes plugins install` checks out). Development happens on `main` with
normal pushes — CI witnesses every commit but does not gate a direct push.
Releasing is deliberate: once a commit on `main` is green, push it to
`stable`, where the pre-push hook runs the same checks locally and the
repository protection blocks a push whose history would be rewritten.
`stable` should always be a descendant of `main`'s last release.

## Licence

MIT. The vendored LaTeX classes and fonts keep their own licences — see
`templates/*/LICENSE.md` and the headers in the class files.
