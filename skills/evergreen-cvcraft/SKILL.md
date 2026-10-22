---
name: evergreen-cvcraft
description: "Use when writing or debugging LaTeX for an Evergreen CV template: the slot contract, class APIs, and build traps."
version: 0.1.0
author: "Bryan Pawlowski, Hermes Agent"
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
