# The Template Configurator — feature plan

An interactive HTML Configurator, usable in the Hermes desktop app, that turns a
set of structured settings into a **new LaTeX template directory** in a career
workspace's `templates.local/`.

Status: planned 2026-10-22. Nothing in this plan is implemented yet.

---

## 1. What Hermes can actually do (verified, not recalled)

Everything below was read out of the running install, not from memory.

### The inline `::preview` widget — real, but wrong for this

`::preview{file="…"}` renders a single HTML file in a sandboxed iframe inside a
chat message. `window.hermes.send(prompt)` (or `data-hermes-send="…"` on any
clickable element) fires a hidden user turn back to the agent.

- Source: `apps/desktop/src/components/assistant-ui/inline-preview-directive.tsx`
- The intent path is hard-capped: **prompt ≤ 500 characters**, one intent per
  second, and an over-length intent is **rejected, never truncated** (line 80,
  line 163).
- The frame is an opaque origin with `sandbox="allow-scripts"`. It cannot reach
  the app, its storage, or the filesystem bridge.

A 500-character ceiling is smaller than a settings payload, and the widget
cannot write files. It is the right tool for a knob panel that pings the agent;
it is the wrong tool for a Configurator that must persist a template.

### The desktop plugin SDK — the right tool

A desktop plugin is one plain-JS ESM file the app loads at runtime, with no
build step. It can contribute panes, pages, palette commands, status-bar chips,
and keybinds. `ctx` gives it:

| Door | Use |
|---|---|
| `ctx.rest(path, opts)` | JSON to the plugin's own FastAPI backend, mounted at `/api/plugins/<id>/` — **profile-aware, namespace-scoped by construction**, no size ceiling |
| `ctx.socket(path, fn)` | the live twin: a WebSocket to the same namespace |
| `ctx.storage.get/set/remove` | plugin-namespaced persistence |
| `host.request(method, params)` | gateway JSON-RPC |
| `host.onEvent(type, fn)` | live gateway events |
| `ctx.os.*` | native notification, reveal-path, clipboard |

The desktop app watches the plugin directory and hot-reloads on save. Only
`@hermes/plugin-sdk`, `react`, and `react/jsx-runtime` may be imported; the file
is **not compiled**, so UI is written with `jsx()` calls, never JSX syntax.

### The unified package — one installable folder, both halves

```
~/.hermes/plugins/<id>/
├── plugin.yaml                   # agent half: tools, hooks, commands
├── __init__.py
├── evergreen/                    # this plugin's Python
├── dashboard/
│   ├── manifest.json             # { "name": "<id>", "api": "plugin_api.py" }
│   └── plugin_api.py             # backend routes → /api/plugins/<id>/
└── desktop/
    └── plugin.js                 # desktop half: panes, commands, ctx.rest
```

The Electron shell copies `desktop/plugin.js` into
`$HERMES_HOME/desktop-plugins/<id>/` and loads it through the same pipeline as
a standalone disk plugin. Installing, sharing, or removing the feature is one
folder.

Three gates, all of which apply to this feature:

1. **The desktop half ships opt-in.** It inventories in *Capabilities → Plugins*
   but stays disabled until the user toggles it. Dropping a package into
   `plugins/` is inert on every surface until they say otherwise.
2. **The Python half is gated separately.** `plugin_api.py` is imported only
   when the plugin is in `plugins.enabled` in `config.yaml` — and `evergreen`
   already is.
3. **The two ids must match.** `ctx.rest('/x')` resolves to
   `/api/plugins/<desktop-plugin-id>/x`, and the backend is mounted at
   `/api/plugins/<dashboard-manifest-name>/`. A unified package uses the same
   id for both (`evergreen`).

### Two consequences the plan has to design around

- **The web dashboard also discovers `dashboard/manifest.json`.** The SPA loads
  every manifest's `entry` as a script and defaults `entry` to
  `dist/index.js`. A manifest with no such file produces a 404 and a
  `LOAD_FAILED` warning in the *browser* dashboard. The desktop app is
  unaffected — it uses `desktop/plugin.js`, not the SPA. **Mitigation: ship a
  no-op `dashboard/dist/index.js`.**
- **`plugin_api.py` is loaded as a standalone module**, not as part of the
  `hermes_plugins.evergreen` package
  (`hermes_cli/web_server_dashboard.py:_mount_one_plugin_api` uses
  `spec_from_file_location` with the name `hermes_dashboard_plugin_<id>`). It
  therefore cannot use relative imports into this plugin's own package; it must
  put the plugin root on `sys.path` and import from there. The result is a
  *second* copy of the module, distinct from the agent half's. That is fine for
  the pure functions involved, and it is noted here because it is invisible
  until it bites.

### The `hermes plugins validate` desktop lint

`hermes_cli/plugin_validate_desktop.py` lints `desktop/plugin.js` at admission.
It refuses: prototype patching, `eval` / `new Function`, dynamic `import()` of
anything outside the SDK, remote `import` specifiers, `createElement('script')`
and `<script>`, and reaching into app DOM via `[data-slot|tour|sidebar|testid]`.
The new pane is written inside that surface from the first line.

This lint is **not** in the repo's CI, because CI cannot run
`hermes plugins validate` (the PyPI release lags the git install and has no
`validate` subcommand). See M2.

---

## 2. What the Evergreen plugin already gives us

- The **template contract** (`templates/README.md`): a directory with
  `template.yaml` and `cv.tex`, slot markers `%HERMES:SLOT <name>` …
  `%HERMES:END`, optional vendored fonts, `fonts.conf`, a `.cls`, and `assets/`.
- Four shipped templates: `plain`, `yaac`, `altacv`, `minimal` — one per
  dialect, plus a `custom` dialect escape hatch.
- The manifest fields the renderer reads: `id`, `name`, `description`,
  `dialect`, `engine`, `main`, `class_files`, `fontconfig`, `fonts_dir`,
  `assets_dir`, `columns`, `page_budget`.
- Discovery: `career_templates` (tool) and `hermes career templates` (CLI).
  `templates.local/<id>/` overrides a shipped template of the same id.
- A build path that already reports **page count** and **overfull-box count**,
  and a CI job that proves every template's content reaches the PDF's text
  layer.

### The gap this feature fills

There is **no settings concept in a template today.** The renderer substitutes
slots and nothing else. A template's colours, column geometry, and type sizes
are hardcoded in its `cv.tex`.

The plan closes that with one seam, and — importantly — **without touching the
renderer.** The Configurator emits a *complete, self-contained template
directory*, exactly like a hand-written one. The existing `career_build` then
renders it with no changes at all. The blast radius is the generator, not the
render path.

---

## 3. The design

### 3.1 The seam: a recipe points at the lines the template already has

> **Revised during M1.** The original design put a `%HERMES:SETTINGS` region
> in each base template's preamble and had the generator replace the whole
> region. Building it showed the region is unnecessary, and costs something:
> every shipped `cv.tex` has to be edited to add markers and to move its
> hardcoded values into them, and a template that is *not* edited correctly
> renders differently for no reason. The region also cannot express a knob
> whose value is one piece of a larger line.

The seam that shipped is narrower and does not touch a single shipped
template. **A recipe names the exact line it rewrites, and the generator
asserts that line matched exactly once.**

```yaml
# templates/plain/recipe.yaml
id: plain
knobs:
  - id: margin
    label: 'Page margin'
    type: length
    default: '0.75in'
    latex: '\usepackage[margin=<<value>>]{geometry}'
```

`latex` is a *pattern*, not a literal: `<<value>>` is where the value goes.
Applying the defaults reproduces the source byte-for-byte, so a template with
a recipe renders **identically** to one without. That is what makes this safe
to ship — the shipped templates are not modified, and the recipe can only
ever change a line it names explicitly.

Three rules keep it honest:

- **Exactly one match.** If a pattern matches zero times (the template drifted)
  or more than once (the rewrite would be ambiguous), generation fails loudly.
  A recipe and a template that have drifted apart must never half-apply.
- **Patterns are substrings, not whole lines**, so editing an unrelated part
  of a line does not break the recipe. Just enough context to be unique.
- **The defaults are a no-op**, asserted by the selftest. A recipe that
  changes the template when applied with its own defaults is a broken recipe.

### 3.2 The recipe: what each base actually supports

The user's four requested setting families do **not** mean the same thing in
each base template, and pretending otherwise is how a Configurator ships
garbage:

| Requested knob | `plain` | `yaac` | `altacv` | `minimal` |
|---|---|---|---|---|
| **colors** | none to change | 1 `\definecolor` (`basecolor`) | 5 `\definecolor` + 8 `\colorlet` derived from them | 10 `\definecolor` (ink, wash, band, gold…) |
| **columns** | single column | class tabular; `\leftcolumn` + `\rightcolumnlength` are *widths* | 2-col paracol, `\columnratio` | 2-col paracol, `\columnratio` + eso-pic sidebar width |
| **word sizes** | `\documentclass` size, `\titleformat` size | fixed by the class | `\namefont`, `\personalinfofont`, `\cvsectionfont`, `\cvsubsectionfont` are all `\renewcommand` | `\documentclass` size; heading size is inside macros |
| **base template** | — | — | — | — |

So "columns" is sometimes a count, sometimes a ratio, sometimes a pair of
widths — and "word sizes" is sometimes a class-fixed value that cannot be
changed at all. A recipe per base template is the honest way to express that.

A recipe is a small YAML file next to the template:

```yaml
# templates/altacv/recipe.yaml
id: altacv
knobs:
  - id: accent
    label: "Accent"
    type: color
    default: "#8F0D0D"
    latex: '\definecolor{PastelRed}{HTML}{%s}'   # %s = hex, no leading '#'
  - id: body
    label: "Body text"
    type: color
    default: "#666666"
    latex: '\definecolor{LightGrey}{HTML}{%s}'
  - id: name_size
    label: "Name size"
    type: enum
    default: "Huge"
    options: ["Huge", "LARGE", "large", "normalsize"]
    latex: '\renewcommand{\namefont}{%s\rmfamily\bfseries}'
  - id: column_ratio
    label: "Column split"
    type: ratio
    default: 0.6
    min: 0.3
    max: 0.8
    latex: '\columnratio{%s}'
```

Each knob names the **exact LaTeX line it replaces**. The generator applies a
replacement and asserts the match count is exactly one — the same rule the
evergreen-resume skill already enforces for hand edits, where a silent
half-applied pass is the known failure mode.

The recipe also declares, per knob, whether the base supports it. A base that
cannot change its type size says so, and the form renders that control as
disabled rather than pretending.

### 3.3 The payload

The pane collects settings and posts one structured object:

```json
{
  "base": "altacv",
  "id": "my-cv",
  "name": "My CV",
  "workspace": "evergreen",
  "settings": {
    "accent": "#8F0D0D",
    "body": "#666666",
    "name_size": "Huge",
    "column_ratio": 0.6
  }
}
```

The backend validates it against the recipe, resolves the LaTeX, and writes
the template directory. The settings are also written into the generated
template as `settings.yaml`, so a generated template records its own provenance
and can be regenerated.

### 3.4 What gets written

```
<workspace>/templates.local/<id>/
├── template.yaml      # new id/name/description; copies the base's dialect,
│                      # engine, class_files, fontconfig, fonts_dir, assets_dir
├── cv.tex             # base cv.tex with the settings region replaced
├── settings.yaml      # what was chosen, and from which base
├── <base>.cls         # copied from the base template
├── fonts/  fonts.conf # copied from the base template
└── assets/            # copied from the base template
```

The class, fonts, and assets are **copied, not referenced**: the template
contract has no notion of inheritance, and `stage()` already copies these
directories next to the build. The cost is disk — the vendored fonts are a few
MB per template — and that cost is called out rather than hidden.

### 3.5 The preview

The user chose a fast schematic preview over a live PDF. It is honest about
what it is and is drawn **client-side** from the recipe plus the chosen values,
so it updates instantly with no backend round trip:

- a page rectangle with the column split drawn at the template's real ratio
- colour swatches for every colour knob
- a type-scale bar for every size knob
- the list of slots the template uses

It is a **schematic, not a render**. It does not attempt to place content, and
the pane labels it as such. The truth is the "Build PDF" button, which runs
the real tectonic build and reports the plugin's existing page count and
overfull-box count.

### 3.6 The surface

A **pane**, docked right of the workspace, ~420px wide, plus a ⌘K command
("Configurator") to focus it. It is a side tool, not a destination, so a pane
beats a full page. The form is generated from the recipe JSON, so adding a knob
to a recipe needs no UI change.

---

## 4. Milestones

Each milestone lands green on the repo's own CI: selftest, manifest check, leak
check, line-ending check, ruff, and render-parity. Every new file must pass
the leak check — **no absolute local paths, no email addresses** — and the
line-ending guard (**LF only**).

### M1 — The seam

*Goal: a generated template compiles and renders, with no UI and no renderer
change.*

**Status: done.** Everything below shipped, and the whole milestone is covered
by CI.

- `schema/recipe.schema.json` and `schema/settings.schema.json` — the two
  schemas, validated with `jsonschema` as the repo already does for
  `career.schema.json`.
- `evergreen/recipe.py` — load and validate a recipe; resolve a settings dict
  into a list of LaTeX snippets; assert each replacement matched exactly once.
- `evergreen/generate.py` — given a base template, a settings dict, and a
  target id, produce the template directory described in §3.4.
- `templates/plain/recipe.yaml` — **`plain` first**, because it has no class
  and no fonts, so it isolates the seam from every other moving part. Four
  knobs: base size, margin, section heading size, bullet spacing.
  **No shipped `cv.tex` was modified** — see the revision note in §3.1.
- `evergreen/test_recipe.py` — the selftest: loads every recipe, asserts
  every pattern matches exactly once, asserts the defaults are a no-op,
  asserts bad settings and drifted recipes are rejected, generates a template,
  and compiles it.
- `scripts/check_generated_parity.py` — the acceptance check. It compiles the
  base *and* a generated template and asserts two things: the generated PDF
  carries every piece of content the base's does, **and** the generated PDF's
  text geometry differs from the base's. The second half matters — a rewrite
  that was accepted and then silently discarded passes a content check on its
  own, because the content is identical either way. Both halves were verified
  to fail when they should.

*Done when:* `plain` can be generated with a changed value and the output PDF
matches the base on content, with the change visible. **Met** — measured: body
text 9.0pt → 10.9pt, left margin 54pt → 72pt, content byte-identical in the
PDF text layer.

*Risks:* the region's exact placement in the preamble (must be after
`\usepackage{xcolor}` and before the class consumes the colour); a `plain`
recipe that does not generalise to the other three bases.

*What M1 changed about the plan:* the `%HERMES:SETTINGS` region was dropped
in favour of pattern-matching (§3.1). This removed the need to edit any
shipped template — the M2 parity risk about byte-for-byte equivalence of
edited templates is now unnecessary, since no template is edited. M2's
per-base recipes must still map each knob to a pattern that matches exactly
once, which is the same rule `plain` now demonstrates.

### M2 — All four bases, the CLI, and the CI gates

*Goal: every shipped template is generatable, and the repo can prove it.*

- Recipes for `yaac`, `altacv`, `minimal` — each knob mapped to the exact
  line it replaces, including the `\colorlet` cascade in `altacv` and the
  eso-pic sidebar geometry in `minimal`. No `cv.tex` edit is needed for any
  of them, which was the point of the §3.1 revision.
- `hermes career generate --base <id> --id <new-id> [--set k=v …]` — the
  headless, scriptable entry point. It is also the agent-facing surface; a
  `career_configure` tool is added in the same pass, which means
  `plugin.yaml`'s `provides_tools` and `scripts/check_manifest.py`'s
  declared-vs-registered comparison both get updated in the same commit.
- New CI job: every shipped template's recipe validates, and generating from
  each recipe produces a template that compiles and passes the existing
  render-parity check. The job must have **teeth** — a planted broken recipe
  is asserted to be rejected — in the same spirit as the existing negative
  tests.
- A self-contained desktop-surface lint in `scripts/`, mirroring the subset of
  `plugin_validate_desktop.py` that CI can run without a new Hermes. Without
  it, `desktop/plugin.js` lands with no CI coverage at all, since
  `hermes plugins validate` cannot run in CI.

*Done when:* all four bases generate, compile, pass parity, and the CLI
produces a template the existing `career_build` renders.

*Risks:* `minimal`'s sidebar geometry is coupled to its text measure — the
evergreen-resume skill records that a panel column's text measure and its rule
measure must agree, and the rule is inset 20pt. A recipe that changes the
column split must move both, or the text runs off the panel. `altacv`'s
`\colorlet` chain means changing `PastelRed` moves `tagline`, `heading`,
`subheading`, and `accent` with it — the recipe must reflect that in the
form's labelling.

### M3 — The backend door

*Goal: the whole feature is reachable over HTTP, with no UI.*

- `dashboard/manifest.json` and `dashboard/plugin_api.py`:
  - `GET /templates` — every base template, its recipe, and its knobs
  - `GET /workspaces` — the workspaces on this machine
  - `POST /generate` — settings → writes `templates.local/<id>/`; returns the
    written paths and the resolved LaTeX
  - `POST /build` — a generated template → real tectonic build → PDF path,
    page count, overfull-box count
- The no-op `dashboard/dist/index.js`, so the browser dashboard does not log a
  load failure for a manifest that has no SPA tab.
- `plugin_api.py` puts the plugin root on `sys.path` and imports from there
  (§1).

*Done when:* the endpoints are exercised against a live `hermes serve`, the
generated directory is read back from disk, and the built PDF is checked by the
existing parity script. **Not** by the pane — the pane is M4.

*Risks:* the API mount is behind `plugins.enabled` (already satisfied) and
behind a hot-mount fallback on first request, so a restart may be needed after
the first deploy. The `hermes serve` process is separate from the CLI's
environment: `tectonic` is found via `shutil.which` plus a `~/.local/bin`
fallback, which the served process must be able to reach.

### M4 — The desktop pane

*Goal: the Configurator, usable in the Hermes desktop app.*

- `desktop/plugin.js` — a pane (docked right, ~420px) and a ⌘K command.
  The form is built from the recipe JSON; the schematic preview is drawn
  client-side; "Generate" and "Build PDF" call `ctx.rest`.
- Written entirely inside the SDK surface: `jsx()` calls, not JSX; only
  `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` imported; no `eval`, no
  dynamic import, no app-DOM reach.
- The pane ships **disabled by default** and the plan includes the
  "Capabilities → Plugins → toggle" step in the user-facing instructions.
- Dev loop: the desktop half is loaded from a *copy* at
  `desktop-plugins/<id>/`, refreshed only on Rescan, an install/update through
  the app, or a restart. Editing the source in place does nothing on screen.
  A **linked checkout** (`ln -s <repo> ~/.hermes/plugins/evergreen`) makes
  every save re-sync and hot-reload. On Windows that needs developer mode or
  admin for the symlink; the fallback is to copy the file and press Rescan.

*Done when:* the pane appears after a reload, no error toast is shown, a
generated template lands on disk, "Build PDF" returns a PDF with the expected
page count, and the settings survive a round trip through the form.

*Risks:* the desktop half's opt-in toggle is a step the user must take and
will be forgotten; the desktop lint's forbidden-pattern list is easy to trip
over accidentally with a dynamic import or an `innerHTML` write.

### M5 — Distribution and documentation

*Goal: the feature ships.*

- Merge to `main`, then `stable`, with the GitHub run all green.
- `templates/README.md` gains the recipe contract (which is what shipped in
  M1); the `evergreen-cvcraft` skill gains the same, so an agent editing a
  template knows the recipe exists and that a recipe must match exactly once.
- A catalog PR to `NousResearch/hermes-agent` adding
  `plugin-catalog/evergreen.yaml` with the reviewed commit SHA.
- The `TASKS.md` entry that points here, and the removal of the "run
  `hermes plugins validate` at release time" item once the desktop lint is in
  CI and the authoritative validator has actually been run against the new
  desktop half.

---

## 5. Decisions taken, and the one question still open

Each of these was a fork in the design. The decision is stated with the
alternative that was rejected and why, so a later reader does not reopen it by
accident.

**A generated template is a normal template directory.** It has no "generated"
marker and the Configurator is not the only way to change it — the files are
plain LaTeX, and the user can edit `cv.tex` by hand exactly like any other
template. *Rejected:* a "generated, regenerate to change" lock, because the
template contract's whole premise is that `cv.tex` is real LaTeX you can edit
freely, and a lock would contradict it.

**The recipe is a sibling `recipe.yaml`, not a `recipe:` block in
`template.yaml`.** The manifest stays exactly the twelve fields the renderer
reads (`id`, `name`, `description`, `dialect`, `engine`, `main`, `class_files`,
`fontconfig`, `fonts_dir`, `assets_dir`, `columns`, `page_budget`), so the
manifest's contract is unchanged and the recipe is purely additive. *Rejected:*
a `recipe:` block, which would put a field in the manifest that no renderer
reads and that `check_manifest.py` would then have to learn about.

**The template author owns the recipe, not the plugin.** A third-party template
in `templates.local/` ships its own `recipe.yaml` and therefore its own knobs,
with no plugin change. The cost is that the recipe format is a public contract
and must be stable. *Rejected:* plugin-owned recipes, which would make every
new template require a plugin release to be configurable.

**A recipe names the lines it rewrites; the base template is not edited.** The
recipe's `latex` pattern points at the line as it already exists in `cv.tex`,
and the generator rewrites exactly that line. The shipped templates are
therefore never modified, and a template with a recipe renders byte-for-byte
identically to one without. *Rejected:* a `%HERMES:SETTINGS` region in each
`cv.tex` (the original design, revised during M1 — see §3.1), which would
have meant editing every shipped template to add markers and move its
hardcoded values in, and which could not express a knob whose value is one
piece of a larger line. The cost of the shipped design is that a value
appears in two places — the template's line and the recipe's pattern — so the
exactly-one-match rule is what keeps them in step, and a drifted recipe is
an error rather than a silent mis-render.

**The still-open question: disk cost.** Each generated template copies the
base's vendored fonts and class file, because the template contract has no
notion of inheritance and `stage()` copies those directories next to the build.
The vendored fonts are a few MB per template. This is fine for a handful of
templates and becomes waste in a workspace that accumulates many. *Options, not
yet decided:* (a) accept the copies; (b) add an inheritance mechanism to the
contract so a generated template references its base's fonts; (c) symlink the
font directory instead of copying it. (b) is the principled fix but expands the
template contract, and (c) breaks the "a template directory is self-contained
and portable" property that lets a template be copied to another machine. This
is deferred to M2, where the real numbers are visible.
