---
name: evergreen-cvcraft
description: "Use when writing or debugging LaTeX for an Evergreen CV template: the slot contract, class APIs, and build traps."
version: 0.1.0
author: "Bryan-Pawlowski"
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [career, cv, latex, tectonic, templates]
    category: productivity
---

# Evergreen — LaTeX craft

Read this before writing or editing a template's `.tex`, or debugging a
build that fails.

## The slot contract

A template is a directory with `template.yaml` and a `cv.tex` holding slot
markers:

```latex
%HERMES:SLOT experience
%HERMES:END
```

Everything between the markers is replaced by the LaTeX the dialect renders
for that slot. Everything outside them is ordinary LaTeX you can edit
freely — that is what makes a template "real LaTeX" rather than a config
file.

The recognised slots are `identity`, `summary`, `skills`, `experience`,
`projects`, `education`, `milestones`, `links`, `languages`, and
`section:<id>` for a user-defined section.

**A slot with no content renders as nothing** — the marker block is removed
and leaves no blank space. **A slot marker for an unknown name is a build
error**, not a silent no-op, so a typo never produces an empty CV.

Full contract: `templates/README.md`.

## Dialects

A dialect is the only part of the contract that is not plain LaTeX. It
knows one template family's idioms.

| dialect | class | key macros |
|---|---|---|
| `plain` | article | `section*`, `itemize` — the bootstrap dialect |
| `yaac` | yaac-another-awesome-cv | `\sectionTitle`, `\experience`, `\project`, `\scholarshipentry`, `\keywordsentry` |
| `altacv` | altacv | `\cvsection`, `\cvevent`, `\cvtag`, `\cvachievement`, `\divider` |
| `minimal` | article + eso-pic | hand-rolled: `\sechead`, `\jobhead`, `\jobdates`, `\jobitems`, `\lchip`, `\faIco` |

`custom` loads a `dialect.py` from the template directory — see
`templates/README.md` for the `render(slot, items, ctx)` signature.

### Verified class APIs

These were read off the vendored `.cls` files. Do not guess them; the
argument orders are not what you would expect.

**yaac** (`yaac-another-awesome-cv.cls`):

- `\experience{END}{title}{org}{country}{START}{body}{tech-list}` — **the
  end date comes first**, and the tech list is a `\foreach` list of plain
  comma-separated names, not a LaTeX list.
- `\project{name}{dates}{links}{description}{tech-list}`
- `\scholarshipentry{date}{description}` inside `scholarship`
- `\keywordsentry{label}{description}` inside `keywords`
- `\name{first}{last}`, `\tagline{...}`, `\socialinfo{...}` are
  **declarations**; `\makecvheader` is called by the template afterwards.
- `\email{...}`, `\smartphone{...}`, `\address{...}`, `\github{...}`,
  `\website{url}{label}`.

**altacv** (`altacv.cls`):

- `\cvevent{title}{org}{date}{location}` — four arguments, in that order.
- `\name{...}` takes **one** argument (the full name); `\personalinfo{...}`
  holds the contact lines; `\makecvheader` comes after.
- `\cvsection{title}`, `\cvsubsection{title}`, `\cvtag{...}`,
  `\cvachievement{icon}{title}{detail}`, `\divider`.

## The horizontal-mode trap

**This is the single most common way these builds fail.**

`\cvsection` (altacv) begins with `\nointerlineskip`, which sets
`\prevdepth`. That is illegal in horizontal mode, and TeX fails with:

```
! You can't use `\prevdepth' in horizontal mode.
```

The constructs that leave you in horizontal mode:

- `\cvevent` — ends without a paragraph break.
- `\divider` — a rule; it does not close the paragraph.
- `\cvtag` — a tikz node; consecutive tags need an explicit `\\[4pt]`.
- `\cvachievement` — a tabularx; it leaves the paragraph open.

So every block that can be followed by a section heading must end with an
explicit `\par`. The dialects do this for you; if you write a custom
dialect, do it too.

The same class of bug appears in the YaAC class's longtable environments,
where a missing `\end{longtable}` or a stray row terminator produces a
different but equally confusing error.

## Build traps

**Tectonic is XeTeX-based.** There is no `--xetex` / `--pdflatex` /
`--lualatex` flag; passing one is an "unexpected argument" error. The
default `--pass default` runs XeTeX + xdvipdfmx. A different TeX *format*
can be requested with `--format`, but the stock bundle ships only the
`latex` format, so `--format lualatex` fails with "failed to open input
file tectonic-format-lualatex.tex". The builder retries on the default
engine and warns.

**`FONTCONFIG_FILE` is mandatory when a template ships fonts.** The
`fonts.conf` uses `<dir prefix="cwd">fonts</dir>`, and fontconfig resolves
that relative to the *working directory*, so the build must run **in** the
directory holding `fonts.conf`. The builder runs the compile in the staged
build directory, which is why this works.

**`--keep-logs` is not optional.** Without it, tectonic in a no-tty run
skips writing the `.log`, so the directory keeps a **stale log from a
previous (possibly failed) build** — and reading it reports errors from a run
that actually succeeded. Treat stdout's `note: Writing <name>.pdf` as the
success signal; the builder checks exactly that.

**stdin must be closed.** A binary that probes stdin can hang the build
forever. The builder passes `stdin=DEVNULL`.

**Never write `.tex` through a shell heredoc.** `printf` treats `\b` in its
format string as a backspace and corrupts words like `\begin`. Use the
file-write tool, or a Python raw string.

**LaTeX comments and content percent signs are different things.** A `%` in
the template's own LaTeX is a comment; a `%` in *profile content* must be
escaped, and the markup layer does that. When checking for escaping bugs,
look at the rendered body, not the whole file, and look for an *unescaped*
percent — a correctly escaped `\%` still contains a `%`.

## Layout facts that are easy to get wrong

- **`\hfill` does not reliably right-align** when the line's left content is
  long. A trailing `\mbox{}` after the right-aligned text is the reliable
  form.
- **A slash is not a legal LaTeX break point.** `tech/design` is one
  unbreakable token and runs past the column edge; `tech-design` breaks.
- **This build has no English hyphenation patterns** (babel and polyglossia
  load, but neither brings patterns for the TU encoding under XeTeX), so
  only words containing a real hyphen can break.
- **A fixed-height band clips its own text.** If a header band is 100pt and
  the content grows to four lines, the last line's descenders are cut
  against the white below. Re-derive the band height when the content
  length changes.
- **A panel column's text measure and its rule measure must agree.** When
  the rules are inset N pt from the panel edge, the text needs the same
  inset — otherwise the text runs past its own section's rule and off the
  panel. Fix the text measure, never the rules.

## Verifying a build

The build's own report is the floor, not the ceiling:

1. `pages` — compare against the template's `page_budget`. Exceeding it is
   a content problem; fix by trimming words, not by shrinking type.
2. `overfull_boxes` — an overfull hbox means a line runs past its column.
   Count them, then attribute each: was it there before your change? An
   overfull inside a table cell with no word off the page is a calibration
   quirk, not a clip.
3. **Render and look.** A successful build can still have an orphaned final
   page (under ~15% fill), a section split across a page break, or a heading
   at the bottom of a page with its content overleaf. The text layer cannot
   see any of this.

## Adding a template

1. Copy `templates/plain/` and edit its `cv.tex` — the LaTeX there is
   deliberately ordinary so you can read it top to bottom.
2. Change `id` and `name` in `template.yaml`.
3. Keep the slot markers where you want content to land.
4. To place slots in a custom layout (columns, minipages, paracol), just
   move the markers; the content follows.
5. If the layout needs macros the `plain` dialect does not emit, write a
   `dialect.py` and set `dialect: custom`.
6. Drop it in the workspace's `templates.local/<id>/` and it overrides a
   shipped template with the same id.

The build copies the template into a scratch directory before compiling, so
a template directory is never written to and never accumulates build
artifacts.

### The recipe, if your template has one

A `recipe.yaml` names the settings a template exposes, each naming the
exact line of `cv.tex` it rewrites. **The shipped templates do not carry
one** — a recipe points at real lines in a real template, so it belongs to
whoever owns that template, not to the plugin. Your own template in
`templates.local/<id>/` can have one.

If you copy a template that has a recipe and edit its `cv.tex`, the
recipe comes with the copy and may no longer match. A pattern that
matches zero times (you changed the line) or more than once is an error,
and the generator refuses to guess.

So after copying a template with a recipe, either delete `recipe.yaml` —
the template is complete without one, it just has no adjustable settings
— or update the patterns to match your new `cv.tex`. A recipe's `latex`
field is a *pattern*, not a literal: `<<value>>` is where the value goes,
and the rest of the pattern must appear in `cv.tex` exactly as written.

To write one, ask for it:

```
hermes career author <template-id> --path <the-template-directory>
```

That proposes the settings the template could expose, proves each one
with a real render, and writes the recipe with the settings that survived
— a setting that changes nothing is left out, because a control that
does nothing is worse than no control. Read it, edit it, and it is yours.

Full contract: `templates/README.md`.

## Developing against a running gateway

A Hermes gateway is a single multiplexed process that serves every profile.
It loads plugin code **at startup**, and `hermes plugins update` only
reloads the gateway's command surface — the message it prints says
`deferred: tools (next session)`, and even a new session served by the
*same* gateway keeps the old tool code in memory.

So after changing plugin code:

```
hermes plugins update evergreen   # pull the new code
hermes gateway restart            # make the new code live
```

Skipping the restart is the trap: the source on disk is new, the tool
behaviour is not, and a test will tell you the fix "did not work".

A second trap: with a gateway running, a session in one profile can be
served the plugin copy of another — the gateway caches one module per
plugin name. When a test profile behaves differently from the default
profile, check which copy is actually loaded before believing the result.

## A discovery tool must not hide the thing it discovers

`list_workspaces` originally called `load()`, which raises when a career
profile fails schema validation, and swallowed the exception with a bare
`continue`. The workspace then vanished from `career_list` entirely.

That is backwards. The listing exists to **find** workspaces, and the
most likely reason a profile fails to validate is that it is half-filled
or was hand-edited — exactly when the caller most needs to find it. A
tool that hides the broken thing cannot tell you it is broken.

**Rule: a discovery tool reports problems, it does not filter them out.**
An invalid workspace is now listed with `profile_status: "invalid"` and
the validation error in `profile_error`. `Workspace.exists()` was
already structural (tolerant of a half-filled profile); the listing has
to agree with it.

**Rule: `except: continue` in a discovery path is a smell.** If the
exception carries information the caller needs, attach it to the result
instead of dropping the result.

**This was found by an agent, not by a test.** The agent created a
workspace, listed zero, and went and diagnosed the profile-validation
path by hand. The selftest passed because it only ever listed
workspaces it had just created. A fixture with a *deliberately invalid*
profile is what catches this class of bug.

## The tool schema and the handler are written separately

A tool's declared schema (the JSON the model sees) and the handler that
reads `params` are two separate pieces of text. When one changes and the
other does not, the model sends exactly what the schema says, the handler
reads a different key, and the tool fails with an error the model cannot
diagnose — it will guess, retry, and often report the tool as broken.

This happened for real: `career_init`'s schema still declared `path`
after the handler had moved to `name`. The agent sent a path, got "A
workspace name is required", worked around it, and reported the mismatch
in its answer. The bug was invisible in the tests because the tests
called the handler directly and never went through the schema.

**Rule: change a tool's parameters in the schema *and* the handler
together, and let the selftest check it.** `test_schema_matches_handlers`
asserts that every declared parameter is read by its handler, that no
required parameter goes unread, and that every workspace-resolving tool
accepts a `name`. It is verified to fail when the drift is reintroduced.

**Rule: exercise a tool change through a real agent turn, not just the
selftest.** The selftest calls handlers directly; only a live agent goes
through the declared schema. A tool change is not done until an agent has
called it without a workaround.

The same trap has a second face in the CLI. `setup_fn` receives the
`ArgumentParser` Hermes created for the command, and `handler_fn` is
called with the *parsed namespace* — it does not receive argv to re-parse.
A CLI wired to the wrong contract still passes a manifest check and
still registers; `hermes career list` simply fails with "unrecognized
arguments". `test_cli_surface` builds the parser the way Hermes does and
calls the handler the way Hermes does, so this cannot drift silently
either.
