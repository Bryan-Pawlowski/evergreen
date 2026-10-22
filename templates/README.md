# Template contract

A **template** is a directory under `templates/` (shipped) or `templates.local/`
(user-supplied) that turns a `profile.yaml` into a `.tex` file. Two files are
required, one optional:

```
templates/<id>/
├── template.yaml     ← manifest (required)
├── cv.tex            ← the LaTeX source with slot markers (required)
├── fonts/            ← vendored font files (optional)
├── fonts.conf        ← fontconfig file pointing at ./fonts (optional)
├── <class>.cls       ← a vendored LaTeX class (optional)
└── assets/           ← images (optional)
```

## `template.yaml`

```yaml
id: yaac                       # directory name; must match
name: "YAAC — Another Awesome CV"
description: "One-column, class-driven, sectioned."
dialect: yaac                  # yaac | altacv | minimal | plain | custom
engine: lualatex               # lualatex | xelatex | pdflatex
main: cv.tex                   # entry file, relative to the template dir
class_files: [yaac-another-awesome-cv.cls]   # copied next to the build
fontconfig: fonts.conf         # set as FONTCONFIG_FILE during build
fonts_dir: fonts               # copied next to the build
assets_dir: assets             # copied next to the build
columns: 1                     # 1 | 2 — used by the layout budget check
page_budget: 2                 # soft target; the build warns when exceeded
```

## The slot marker system

`cv.tex` is **real LaTeX** — you can edit any of it. The plugin only replaces
the regions between markers:

```latex
%HERMES:SLOT experience
%HERMES:END
```

Everything between `%HERMES:SLOT <name>` and `%HERMES:END` is replaced by the
LaTeX the dialect renders for that slot. Outside those regions your LaTeX is
untouched, so a template can place slots in any layout it likes (columns,
paracol, minipages).

Recognised slot names:

| slot | content |
|---|---|
| `identity` | name, tagline, contact lines |
| `summary` | the headline paragraph |
| `skills` | skill groups |
| `experience` | work history |
| `projects` | selected projects |
| `education` | education entries |
| `milestones` | career milestones |
| `links` | personal links |
| `languages` | spoken languages |
| `section:<id>` | a user-defined section from `profile.yaml` |

A slot with no content renders as nothing (the marker block is removed, leaving
no blank space). A slot marker for an unknown name is a build error, not a
silent no-op.

## Dialects

A **dialect** knows how to emit LaTeX for each slot. It is the only part of
the template contract that is not plain LaTeX.

- `yaac` — the `yaac-another-awesome-cv` class: `\sectionTitle`, `experiences`
  environment, `keywordstable`.
- `altacv` — the `altacv` class: `\cvsection`, `\cvevent`, `\cvtag`.
- `minimal` — a bare `article` + `eso-pic` layout with hand-rolled macros.
- `plain` — a generic `article` renderer using only `section*` and `itemize`.
  Use it to bootstrap a new template before writing a dialect.
- `custom` — your own Python module in the template directory (see below).

### Custom dialects

Set `dialect: custom` and add `dialect.py` next to `template.yaml`:

```python
def render(slot, items, ctx):
    """Return the LaTeX for one slot.

    slot  -- the slot name, e.g. "experience"
    items -- the already-selected profile items for that slot
             (a list of dicts, matching the profile schema)
    ctx   -- {"profile": dict, "esc": fn, "inline": fn, "meta": dict}
    """
    out = []
    for it in items:
        out.append(r"\textbf{%s}" % ctx["esc"](it["role"]))
    return "\n".join(out)
```

`ctx["esc"]` escapes plain text for LaTeX; `ctx["inline"]` converts the
inline markup (`**bold**`, `_italic_`, `[label](url)`) to LaTeX. Use both
rather than writing LaTeX by hand — that is what keeps a profile file
portable across templates.

## Inline markup

Profile text is **plain text with a small markup**, never raw LaTeX. This
keeps a profile file editable by non-LaTeX people, keeps it portable between
templates, and stops a profile from injecting arbitrary macros.

| markup | renders as |
|---|---|
| `**bold**` | `\textbf{bold}` |
| `_italic_` | `\textit{italic}` |
| `` `code` `` | `\texttt{code}` |
| `[label](url)` | `\href{url}{label}` |

Everything else is escaped. An en-dash range (`10--12`) is written literally as
`10–12`; the renderer maps the en-dash to a LaTeX en-dash.

## Adding a template

1. Copy an existing template directory.
2. Change `id`, `name`, and the LaTeX in `cv.tex` to taste.
3. Keep the slot markers where you want content to land.
4. Drop it in `templates.local/<id>/` in your workspace (or `templates/<id>/`
   if you are contributing it back).
5. `hermes career templates` lists what is available; `hermes career build
   --template <id>` renders with it.

The build copies the template into a scratch directory before compiling, so
your template directory is never written to and never accumulates build
artifacts.
