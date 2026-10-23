# Template contract

A **template** is a directory under `templates/` (shipped) or `templates.local/`
(user-supplied) that turns a `career.yaml` into a `.tex` file. Two files are
required, one optional:

```
templates/<id>/
├── template.yaml     ← manifest (required)
├── cv.tex            ← the LaTeX source with slot markers (required)
├── recipe.yaml       ← the settings this template exposes (optional)
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
| `section:<id>` | a user-defined section from `career.yaml` |

A slot with no content renders as nothing (the marker block is removed, leaving
no blank space). A slot marker for an unknown name is a build error, not a
silent no-op.

## `recipe.yaml` — the settings a template exposes

Optional. A recipe declares which settings a template lets someone change, and
which line of `cv.tex` each setting rewrites. It is what the Configurator
builds its form from, and what `hermes career generate` reads.

**The shipped templates do not carry one.** A recipe points at real lines in
a real template, so it belongs to whoever owns that template, not to the
plugin. A template of your own in `templates.local/<id>/` can have one, and
`hermes career author <id> --path <dir>` will write it for you.

```yaml
id: plain                       # must match the template's id
name: 'Plain'
knobs:
  - id: margin
    label: 'Page margin'        # the label shown in the UI
    description: 'The margin around the page.'
    type: length                # enum | length | text | number | color
    default: '0.75in'
    latex: '\usepackage[margin=<<value>>]{geometry}'
```

`latex` is a **pattern**, not a literal: `<<value>>` is where the setting's
value goes. The generator finds that pattern in `cv.tex` and replaces it.

Three rules, all enforced:

- **The pattern must match exactly once.** Zero matches means the template
  drifted away from the recipe; more than one means the rewrite would be
  ambiguous. Both are errors — a recipe never half-applies.
- **Patterns are substrings, not whole lines.** Keep just enough context to
  be unique, so editing an unrelated part of a line does not break the recipe.
- **Applying the defaults changes nothing.** A template with a recipe renders
  byte-for-byte identically to one without, so adding a recipe is safe.

A template without a recipe is still a complete template — it just has no
adjustable settings, and the Configurator offers nothing to adjust.

### A setting is only real once a render proves it

A setting that is accepted and then changes nothing is a control that
visibly does nothing, and the user cannot tell it apart from one that
worked. So a setting is not a setting until a render proves that changing
it, on its own, changes the page.

The check is **per knob**, not on the settings as a whole — changing every
setting at once would let a dead knob hide behind a live one — and it is
made against **more than one profile**, because a setting can be dead for
ordinary content and live for content that overflows. The comparison is on
the rendered **pixels**, not on the text: a text probe cannot see a change
that only affects an image overlay.

`hermes career author` runs that check, so the recipe it writes holds only
settings known to change the page. Measured on the shipped `minimal`
template: `emergencystretch` is a real LaTeX setting that the generator
proposes without complaint, and that no ordinary content makes visible —
so it is left out.

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
