# The Template Configurator — feature plan

An interactive HTML Configurator, usable in the Hermes desktop app, that turns a
set of structured settings into a **new LaTeX template directory** in a career
workspace's `templates.local/`.

Status: in progress on `feature/template-configurator`. **M1, M2 and M3 are
done**; M4 (the desktop pane) and M5 (distribution) are not.
Draft PR: <https://github.com/Bryan-Pawlowski/evergreen/pull/1>.

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
    label: 'Accent'
    type: color
    default: '8F0D0D'
    latex: '\definecolor{PastelRed}{HTML}{<<value>>}'   # hex, no leading '#'
  - id: body
    label: 'Body text'
    type: color
    default: '666666'
    latex: '\definecolor{LightGrey}{HTML}{<<value>>}'
  - id: name_size
    label: 'Name size'
    type: enum
    default: 'Huge'
    options: ['Huge', 'LARGE', 'large', 'normalsize']
    latex: '\renewcommand{\namefont}{<<value>>\rmfamily\bfseries}'
  - id: column_ratio
    label: 'Column split'
    type: number
    default: 0.6
    latex: '\columnratio{<<value>>}'
```

> The placeholder is `<<value>>`, not `%s`. A brace-delimited form was
> rejected during M1: LaTeX is full of braces, so a pattern like
> `\titleformat{\section}{<<value>>\bfseries}` must be readable at a glance
> or a template author cannot tell where the value goes. The shipped
> `templates/plain/recipe.yaml` is the reference.

Each knob names the **exact LaTeX line it replaces**. The generator applies a
replacement and asserts the match count is exactly one — the same rule the
evergreen-resume skill already enforces for hand edits, where a silent
half-applied pass is the known failure mode.

The recipe also declares, per knob, whether the base supports it. A base that
cannot change its type size says so, and the form renders that control as
disabled rather than pretending.

### 3.2b Is a *recipe generator* part of this feature?

**Answer: yes, and it is more valuable than the Configurator itself — but
it is a different tool, and it is not a code generator.** It reads a
template and *proposes* a recipe; a human decides which proposals are
real.

The question was asked during M1 and answered with measurement rather than
opinion. The finding is that **detection is mechanical and semantics are
not**, and the seam between those two is exactly where a generator earns
its keep.

Detection is easy. Scanning the four shipped templates for candidate
settings finds:

| template | colour literals | length literals | size literals | other |
|---|---|---|---|---|
| `plain` | 0 | 2 | 1 | — |
| `yaac` | 1 | 2 | 0 | `\documentclass` options are class options (`red`), not values |
| `altacv` | 5 | 0 | 1 | 8 `\colorlet`s deriving from the 5; 8 `\renewcommand` fonts |
| `minimal` | 10 | 4 (3 literal) | 1 | **6 of the 10 colours are never referenced** |

Every one of those 16 colour literals is a syntactically trivial find. A
regex finds them all. So the *mechanical* half of a recipe generator is
easy — and the *semantic* half is where the danger is, in three measured
ways:

1. **A colour knob can be a no-op.** `minimal`'s `Band`, `ColDark`,
   `ColWash`, `Paper`, `Mist` and `GoldenEarth` are declared but never
   referenced: the band and the dark column are **PNG images**
   (`assets/banner.png`, `assets/colbg.png`), and the `\definecolor`
   entries only document what the pixels already are. Measured: changing
   `Band` from `1E2531` to `FF0000` produces a **byte-identical render** —
   same fills, same images, same page count. A generator that offered
   `Band` as a colour knob would ship a control that visibly does
   nothing, which is worse than not offering it.

2. **A derived colour is not a settable value.** `altacv`'s 8 `\colorlet`s
   point at the 5 base colours. Setting `tagline` to a hex is not just
   unusual — it is a **hard LaTeX error** (`\colorlet{tagline}{0000FF}`
   fails to compile; xcolor wants a colour *name*). Only the 5 bases are
   settable, and setting one moves the derived ones with it. Measured:
   changing `PastelRed` moves exactly the 10 text spans that use it,
   leaving the other 40 spans alone. The generator cannot know that
   `PastelRed` is the right *unit* of choice; a human does.

3. **A detected line is not always a settable line.** `yaac`'s
   `\documentclass[letterpaper,localFont,alternative,red]` contains `red`,
   a class *option* that selects a scheme, not a value. `minimal`'s
   `\setlength{\baselineskip}{1.15\baselineskip}` contains a macro, so it
   is not a length literal. And several lines appear **more than once**
   (`altacv` has `\renewcommand{\familydefault}{\sfdefault}` twice;
   `minimal` has 4 identical `\secheadrule` lines), which the
   exactly-one-match rule rejects outright.

4. **A template's own line can be overridden by the dialect.** The
   `plain` template sets its bullet spacing in `\setlist`, and the
   `plain` dialect *also* wrote `\itemsep2pt` inline inside
   `\begin{itemize}` — the dialect's copy won, and the template's line
   was dead. This one is the nastiest because the line looks perfectly
   settable and the LaTeX reads correctly; the knob was accepted, the
   generation succeeded, and the rendered PDF was byte-identical. It
   was found by the per-knob liveness check, not by reading the LaTeX.
   The generator cannot see this class of trap at all — it requires
   knowing what the *renderer* emits.

5. **A template's own line can be overridden by the *class*.** The
   `yaac` template declares `\definecolor{basecolor}{HTML}{8F0D0D}` and
   says in its own comment that changing the hex re-brands the CV. It
   does not. The vendored class does `\colorlet{linkcolor}{basecolor}`
   **at load time**, so the class's own `red` value (`b30000`) was
   already frozen into `linkcolor` before the template's
   `\definecolor` ran. Measured: the template's `8F0D0D` appeared
   **nowhere** in the rendered PDF, and changing it to anything else
   changed nothing — the class's `b30000` was what rendered. Fixed by
   re-deriving the class's three derived colours after the template's
   `\definecolor`; the template's hex now renders, and a `basecolor`
   knob is live. **That fix lives in the workspace, not the repo**
   (§3.2d) — a colour change to a shipped template reaches every
   installer. The generator cannot see this class of trap either: it
   requires reading the vendored class file, and the class's
   *load-time* ordering is the whole mechanism.

   The general lesson: **a setting's value can be overridden by
   anything loaded before it** — the dialect, the class, or a package.
   A recipe names a line; the line is not necessarily the one that wins.

So the generator's real job is not to find settings — it is to **propose
candidates and flag the ones that are traps**, so the human spends their
attention on the semantic judgement rather than on transcription.

### 3.2d The boundary: the repo ships the mechanism, the workspace ships the taste

> **Decided after the M1 audit.** The audit found two defects in the
> *shipped* templates — `yaac`'s accent colour never reaching the page,
> and the `plain` dialect overriding its own template's bullet spacing.
> Both were fixed in the repo first. That was wrong: the plugin repo is
> the distribution channel, and a colour change or a renderer change
> there reaches every installer, not just this workspace. **The repo
> carries the mechanism; the workspace carries the choices.**

Concretely, the repo contains:

- the recipe engine, the generator, the proposal engine, the liveness
  check, the schemas, the tests, and the CI gates — the *capability*;
- **no recipe at all.** A recipe names real lines in a real template, so
  it belongs to whoever owns that template. The shipped templates carry
  none, and the repo's own tests derive one at run time with the same
  engine the Configurator uses.

The repo contains **no** change to any shipped template's colours, and
**no** change to shipped renderer behaviour. The shipped templates and
the renderer are byte-for-byte identical to `main` — proved by comparing
the rendered *pixels* of all four templates, not by reading the diff:

```
plain    d611b887e9d515ea   == main
yaac     4517512fba3714c0   == main
altacv   926ab85e59346534   == main
minimal  7aa8b3a215231b48   == main
```

The workspace contains:

- `templates.local/evergreen/cv.tex` — the accent-colour fix, with the
  class's three derived colours re-derived after the template's
  `\definecolor`, so the hex the template declares is the hex that
  renders. **Verified**: the workspace PDF now carries `#8F0D0D`; the
  backup of the same file still carries the class's `#b30000`.
- `templates.local/evergreen/recipe.yaml` — the workspace template's own
  recipe, with three settings (`basecolor`, `leftcolumn`,
  `rightcolumnlength`), each proved live by a render.
- any future knob whose fix would require touching the renderer.

**Why this is the right boundary.** A recipe is a statement about which
lines of *this* template are worth exposing. The moment a value becomes
a *choice* — a colour, a width — it belongs to the workspace, because
the plugin's job is to make the choice possible, not to make it. And a
change to a shipped template or to the renderer is a change to the
plugin's behaviour for everybody, which is a release decision, not a
feature commit.

The consequence for M2: **no recipe is authored in the repo.** The
repo's role is to make them possible and to prove the seam works — and
it proves it by deriving a recipe for each shipped template at run time
and taking it all the way through to a compiled PDF. That is a
stronger gate than a hand-written recipe would be, because it exercises
the whole chain: propose, author, apply, generate, render.

One more consequence, measured: the `plain` template's "bullet spacing"
knob was dead, because the `plain` dialect writes an inline `\itemsep`
that overrides the template's `\setlist`. Making it live needs the
dialect changed — shipped renderer behaviour — so it does not ship. The
knob was replaced with "bullet indent" (`leftmargin`), which the
dialect does *not* override, so it is live. The lesson generalises: **a
knob that is dead because the renderer overrides the template's own
value is a knob the repo cannot offer.** The proposal engine now
*detects* this case, and flags it.

**The per-knob liveness check is what makes any of this trustworthy.**
A candidate is not a setting until a render proves that changing it
changes the page. The generator proposes; the liveness check disposes.
That is the division of labour, and it is why the generator can be
allowed to guess. It is implemented in `evergreen/liveness.py`, and it
is wired into the authoring path: `hermes career author` proposes,
proves, and writes the recipe **with the dead settings left out**.

**The liveness check must not judge from one profile.** A setting can
be dead for ordinary content and live for content that overflows, so
the check runs each knob against **two** profiles — the caller's own,
plus a stress profile with an unbreakable long word — and requires the
knob to change the page for *some* profile, not for every one.

**And it must judge from the pixels, not the text.** A text probe
reports font sizes and left edges; it cannot see a change that only
affects an image overlay, so it reports a false "no change" for a knob
that plainly moved something. `minimal`'s band is a PNG.

**What the check actually found.** `emergencystretch` is a real LaTeX
setting, set in the `minimal` template, and the proposal engine has no
reason to distrust it. The audit assumed a stress profile would make it
visible. Measured against the real renderer, **it is not**: no content
the check tried — a 34-character word, a 50-character word, a
paragraph of long words, a repeated long word — makes the pages differ
at all. So it is dead, the gate excludes it, and the recipe does not
offer it. The general lesson: **the generator cannot know this, so the
render decides.**

**Where it fits.** As a helper in the authoring path, not a runtime
component. Two commands, and the order matters:

- `hermes career recipe <template>` — read a template, propose a
  `recipe.yaml` skeleton with the mechanical candidates filled in, and
  mark every candidate that needs a human decision (a colour that is
  declared but unreferenced, a derived colour, a class option, a
  non-literal value, an ambiguous line, a macro that is never used, a
  value the dialect writes inline). **It writes nothing.**
- `hermes career author <template> --path <dir>` — propose, then
  **prove** each proposal with a real render, then write the recipe with
  the settings that survived. The traps never become knobs, and the
  settings that change nothing are left out.
- This is what makes the plan's "the template author owns the recipe"
  decision *practical*: today, authoring a recipe means hand-transcribing
  LaTeX patterns, which is exactly the error-prone part. A generator that
  transcribes and flags the traps removes the transcription and keeps the
  judgement.

**Where it does not fit.** The generator must not write a recipe
*unattended*, on the strength of the proposals alone. Every trap above
produces a recipe that is syntactically valid and semantically wrong,
and the only defence is a render plus a human. What the implementation
does instead is put the **render** in the loop: the authoring path
proposes, proves, and writes only what a render says is real, and it
says plainly which settings it left out and why. The human still edits
the result; the machine just refuses to hand over a control that does
nothing.

**Milestone.** M2, and it is done. The generator is the tool that
*writes* a recipe, so building it first is strictly better than writing
recipes by hand and then automating the process. It also earns its keep
on third-party templates in `templates.local/`, which is the case the
plan's "the template author owns the recipe" decision was made for.

### 3.2c The audit: what each base actually has

A detector was run over all four shipped templates and every candidate
was liveness-tested (one knob at a time, against a real render). This is
the input M2's recipes are written from, and it is the generator's
prototype. Compiles are deterministic — the same template compiled twice
produces a byte-identical fingerprint — so these results are stable.

| template | LIVE candidates | DEAD (and why) |
|---|---|---|
| `plain` | `\parindent`, `\parskip`, documentclass size, `leftmargin`, `margin` | `itemsep` — the `plain` dialect writes an inline `\itemsep` that overrides the template's `\setlist` |
| `yaac` | `\leftcolumn`, `\rightcolumnlength` | `basecolor` — the class freezes `linkcolor` at load time (§3.2b trap 5; **fixed in the workspace**, §3.2d) |
| `altacv` | all 5 base colours, `\columnratio`, the 4 font sizes | — (the 8 `\colorlet`s are derived, not settable) |
| `minimal` | `\parindent`, `\bandH`, `\washW`, `\secspace`, `\rulegap`, `\headgap`, `\jobspace`, `\titlegap`, `\bulletspace`, `\itemspace`, `\columnratio`, `Ink`, `InkSoft`, `RuleGold`, `Paper`, `Mist`, `GoldenEarth` | `Band`, `ColDark`, `ColWash` — the pixels are a PNG; `IcoCol` — `\faIco` is redefined later to use `RuleGold`; `\leftcolW` — defined, never used; `\emergencystretch` — real, but nothing makes it visible (§3.2d) |

Two results deserve a note:

- **`Paper`, `Mist` and `GoldenEarth` in `minimal` are LIVE but appear
  nowhere in `cv.tex`.** They are referenced by the *dialect*
  (`dialects.py` emits `\color{Paper}`, `\color{Mist}`,
  `\color{GoldenEarth}`), not by the template. A generator that only
  reads `cv.tex` would wrongly discard three live settings. It must
  read the dialect too.
- **`\emergencystretch` in `minimal` reads DEAD, and is.** The audit
  assumed it was "arguably not dead" — that it would show up for
  content that overflows. The implemented liveness check, against the
  real renderer and four different stress contents, says otherwise: the
  pages are byte-identical. The two-profile rule stays, because a
  setting can still be live only under stress; this one just is not.
  The generator cannot tell the difference, which is exactly why the
  render decides.

The generator's input is therefore **three** sources, not one: the
template's `cv.tex`, the vendored class file, and the dialect the
template selects. A candidate is only a candidate once all three have
been consulted.

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

*What M1 changed about the plan, second:* the plan said the renderer would
not be touched. One dialect line *had* to change, to make the `plain`
template's own bullet spacing adjustable — and then it did not ship,
because a change to the renderer is a change to the plugin's behaviour
for everybody (§3.2d). The knob it would have enabled was replaced with
one that is live without any renderer change. The lesson is recorded in
M2's risks: a dialect that hardcodes a value can make a template's own
setting dead, and only the per-knob liveness check finds it.

*What M1 changed about the plan, third:* the plan said the three
remaining recipes would be written in the repo. They will not be. A
recipe carrying a *chosen* value is a choice, and a choice belongs in
the workspace (§3.2d). The repo ships the mechanism and proves the
seam; the workspace holds the taste.

### M2 — All four bases, the CLI, and the CI gates

*Goal: every shipped template is generatable, and the repo can prove it.*

**Status: done.** What shipped, and where each piece lives:

- **The recipe generator** (`evergreen/propose.py`). It reads three
  sources — the template's `cv.tex`, its vendored class, and its
  dialect — and proposes the settings the template could expose. It
  flags the traps: a colour that is declared but never referenced, a
  derived colour, a colour the class freezes at load time, a colour
  only reachable through a macro that is redefined later, a class
  option, a non-literal value, an ambiguous line, a macro that is
  never used, and a value the dialect writes inline. It writes nothing
  on its own.
  - `hermes career recipe <template>` — report the proposals and the
    traps. Read-only.
  - `hermes career author <template> --path <dir>` — propose, **prove**,
    then write the recipe with the settings that survived. The dead
    ones are left out, and the command says which and why.
- **The liveness gate** (`evergreen/liveness.py`). Per knob, one knob
  at a time, against a real render, compared on a **pixel fingerprint**
  rather than the text geometry — a text probe cannot see a change that
  only affects an image overlay, and would report a false "no change"
  for a knob that plainly moved something. Run against more than one
  profile, because a setting can be live only under content that
  overflows.
- **`hermes career generate --base <id> --id <new-id> [--set k=v …]`**
  — the headless, scriptable entry point, and the agent-facing
  `career_configure` tool in the same pass. `plugin.yaml`'s
  `provides_tools` and `scripts/check_manifest.py`'s declared-vs-
  registered comparison both got updated in the same commit.
- **The CI gates.** `scripts/check_generated_parity.py` proves a
  generated template carries the base's content and shows the change.
  It derives its own recipe at run time with the same engine, so the
  repo carries no concrete recipe and the gate still exercises the
  whole chain — propose, author, apply, generate, render — which is
  stronger than a hand-written recipe would be. `evergreen/test_recipe.py`
  and `evergreen/test_propose.py` cover the rest.
- **The workspace recipe** — `templates.local/evergreen/recipe.yaml`
  in the active workspace, with three settings, each proved live.

*What is not done, and is M3+:* the desktop pane and the distribution. (The
backend door shipped in M3.) The desktop-surface lint (`scripts/`, mirroring the
subset of `plugin_validate_desktop.py` that CI can run without a new Hermes) is
still outstanding, and it belongs with M4, where `desktop/plugin.js` first
exists to be linted.

*Done when:* all four bases generate, compile, pass parity, and the CLI
produces a template the existing `career_build` renders. Every knob of
every recipe is proven live by the per-knob check. **Met**, with one
finding: `minimal`'s `emergencystretch` is not live and is excluded
(§3.2d).

*Risks:* `minimal`'s sidebar geometry is coupled to its text measure — the
evergreen-resume skill records that a panel column's text measure and its rule
measure must agree, and the rule is inset 20pt. A recipe that changes the
column split must move both, or the text runs off the panel. `altacv`'s
`\colorlet` chain means changing `PastelRed` moves `tagline`, `heading`,
`subheading`, and `accent` with it — the recipe must reflect that in the
form's labelling. And a dialect that hardcodes a value inline can make a
template's own setting dead — the `plain` dialect's `\itemsep2pt` did
exactly that, and was found by the per-knob liveness check rather than by
reading the LaTeX. When a recipe knob names a value the *dialect* also
sets, the dialect's copy wins and the knob is a no-op.

### M3 — The backend door

*Goal: the whole feature is reachable over HTTP, with no UI.*

**Status: done.** What shipped:

- `dashboard/manifest.json` — `name: evergreen` (the mount prefix *and* the
  desktop id, so the two cannot drift), `api: plugin_api.py`, `entry:
  dist/index.js`, and a **hidden** tab (`tab.hidden`), because the Configurator
  is a desktop pane, not a page in the web dashboard.
- `dashboard/plugin_api.py` — the four routes. Every handler is a thin wrapper
  over the plugin's own Python (`career_build`, `generate_template`,
  `load_recipe`, `discover_templates`), so the door and `hermes career ...`
  cannot drift. Handlers are plain `def`, not `async def`: FastAPI runs them in
  its threadpool, so a tectonic build cannot block the event loop the rest of
  the dashboard is served from.
  - `GET /templates?workspace=` — every template, its recipe, its knobs, and
    `generate_from` (a template is a base only if its recipe loads *and* its id
    matches the template's). A template that carries a broken recipe is
    reported in `recipe_error`, not dropped.
  - `GET /workspaces` — the named workspaces under the Hermes home plus the
    configured `workspace_path`, each with its own `templates.local` ids.
  - `POST /generate` — body `{base, id, name?, description?, workspace?,
    settings}`; writes `templates.local/<id>/`, **reads the result back off
    disk**, and returns the paths plus the resolved LaTeX per knob (with
    `changed` marking the caller's own choices).
  - `POST /build` — `{template, workspace?, target?}`; 503 when tectonic is
    absent (with the install hint), 422 when the template does not compile,
    and otherwise the PDF path, page count and overfull-box count.
- The no-op `dashboard/dist/index.js`, so the browser dashboard does not log a
  load failure for a manifest with no SPA tab. It is a **classic script**, not
  an ES module (the SPA appends a `<script>`), and it registers a component
  that renders `null` — otherwise the loader reports `NO_REGISTER` for a plugin
  that loads but registers nothing. `.gitignore`'s `dist/` rule would have kept
  this file out of the repo; the fix is a `!/dashboard/dist/` directory
  negation (git cannot re-include a file under an excluded directory).
- `plugin_api.py` puts the plugin root on `sys.path` and imports from there
  (§1).
- `evergreen/test_dashboard_api.py` — 66 checks, run by CI. It loads
  `plugin_api.py` **the way Hermes loads it** (standalone module,
  `spec_from_file_location`, no package context) and mounts it at the prefix
  taken from the manifest, so a drifted manifest name fails the test instead of
  404ing in the app. A separate interpreter, started from an unrelated working
  directory with no `PYTHONPATH`, proves the `sys.path` bootstrap. Every route
  is then called over HTTP against a throwaway Hermes home, and each answer is
  checked **against the filesystem**, never against the handler's own return
  value.
- `scripts/check_manifest.py` now checks the dashboard half too: the manifest's
  `name` must equal `plugin.yaml`'s (gate 3 of §1), `api` and `entry` must be
  relative paths that exist inside `dashboard/`, and the versions must match.
  Verified to fail on a drifted name and on a missing entry file.
- `evergreen/generate.py` — **a template id is now validated** (`validate_template_id`).
  See the finding below.

**The finding: the door needed a check the CLI did not have.** A template id
becomes a directory name under `templates.local/`, and nothing validated it:
`hermes career generate --id ../../elsewhere` — and, once the route existed,
an HTTP request with the same id — would have written outside the workspace.
The fix is one function in `generate.py`, deliberately *there* rather than in
the route, because all three callers (CLI, tool, HTTP) go through
`generate_template`. The route maps the refusal to a 400.

**Measured, against a live `hermes serve`** (isolated, loopback, temp
`HERMES_HOME` holding a copy of this branch as the plugin, the real workspace
passed by path):

```
GET  /workspaces                       200  {workspaces: [evergreen], default: evergreen}
GET  /templates?workspace=<path>       200  bases: [evergreen]; the three knobs,
                                             basecolor/leftcolumn/rightcolumnlength
POST /generate  {basecolor: #1F6FEB,
                 rightcolumnlength: 13.8cm}
                                       200  templates.local/m3live/ on disk;
                                             cv.tex carries 1F6FEB and 13.8cm;
                                             settings.yaml says base: evergreen
POST /build                            200  2 pages, 0 overfull boxes,
                                             out/cv-m3live.pdf
```

The built PDF's text layer was read back with the render-parity script's own
extractor (`_read_text_layer`): 5686 characters, the profile's headline and
contact block present. The refusals were exercised live too and each one is
specific: an unknown setting 400 (naming what the template offers), a duplicate
id 409, `--id ../escape` 400, a base with no recipe 400 (naming
`hermes career author`), an unknown template 404, an unknown workspace 404, and
no session token 401.

**Two facts about the mount, verified rather than assumed.** On a loopback bind
the dashboard gate is off, but `/api/*` still requires the SPA's session token
(`X-Hermes-Session-Token`, or the value injected into the page as
`__HERMES_SESSION_TOKEN__`) — so "loopback means unauthenticated" is wrong, and
a caller that is not the SPA has to present that token. And the plugin routes
are mounted at `/api/plugins/<manifest name>/`, i.e. the manifest's `name` *is*
the desktop id; there is no separate place to set it.

*Done when:* the endpoints are exercised against a live `hermes serve`, the
generated directory is read back from disk, and the built PDF is checked by the
existing parity script. **Not** by the pane — the pane is M4. **Met**, above.

**What M3 changed about the plan:**

- **A workspace outside the Hermes home is addressed by path.** `GET
  /workspaces` can only name the workspaces directly under the Hermes home (and
  the configured `workspace_path`); this machine's real workspace lives at a
  path of its own, so the door accepts a path as well as a name. There is no
  registry to consult — by design (§5) — so **M4's pane needs a way to enter or
  pick a path**, or the operator sets `plugins.entries.evergreen.settings.
  workspace_path`.
- **The door refuses to guess a workspace from its working directory.** The
  agent half's `find_workspace()` walks up from the cwd; for a server that is
  meaningless (and dangerous — it is whatever directory the gateway started
  in), so a request either names its workspace or there is exactly one.
- **A generated template does not carry the recipe that made it.** §3.4 lists
  `settings.yaml` and not `recipe.yaml`, so re-opening `m3live` in the
  Configurator would offer **no knobs** — the settings are recorded, the lines
  they rewrote are not. *Open question for M4/M5:* copy the base's recipe into
  the generated directory with its `id` rewritten to the new template (making a
  generated template itself configurable), or leave generation as a one-shot
  and treat the base as the only thing that is ever configured. Not decided
  here, because it changes M2's artifact.

*Risks:* the API mount is behind `plugins.enabled` (already satisfied) and
behind a hot-mount fallback on first request, so a restart may be needed after
the first deploy. The `hermes serve` process is separate from the CLI's
environment: `tectonic` is found via `shutil.which` plus a `~/.local/bin`
fallback, which the served process must be able to reach — **verified**: the
served process found `~/.local/bin/tectonic.EXE` and built in 1.5 s.


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
new template require a plugin release to be configurable. **Extended during
M2:** the *shipped* templates carry no recipe at all. A recipe points at real
lines in a real template, so it belongs to whoever owns that template — the
plugin's job is to make the choice possible, not to make it. The repo's own
tests therefore derive a recipe at run time, with the same engine the
Configurator uses, and take it all the way to a compiled PDF.

**The generator proposes; the render disposes.** The recipe generator reads
the template, its class, and its dialect, and proposes the settings the
template could expose — flagging the traps it can see. It does not decide
whether a setting is real, because it cannot: only a render can show that
changing a setting changes the page. So the authoring path is propose, prove,
write-what-survived, and the settings that change nothing are left out.
*Rejected:* the generator writing a finished recipe unattended, which would
ship controls that do nothing.

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
