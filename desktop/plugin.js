/**
 * Evergreen — the Configurator's desktop half.
 *
 * A pane docked on the right, plus a ⌘K command that reveals it. The pane talks
 * to the plugin's own backend (`ctx.rest` → `/api/plugins/evergreen/…`), which
 * is the same Python `hermes career …` runs: the door validates the settings,
 * writes `templates.local/<id>/`, and builds the PDF. Nothing here re-implements
 * a recipe rule — a form that did would be a second place for
 * "exactly one match" and "the defaults are a no-op" to drift.
 *
 * Three things are load-bearing, and each is why the code looks the way it does:
 *
 * 1. **The form is derived from the recipe JSON**, not hardcoded. A template's
 *    knobs come back from `GET /templates`, so a template that gains a knob
 *    needs no change here. That is the whole point of the recipe seam.
 *
 * 2. **The preview is a schematic, and says so.** It is drawn client-side from
 *    the values (a page rectangle at the chosen column ratio, a swatch per
 *    colour knob, a bar per size knob), so typing a colour updates the drawing
 *    with no round trip. What it cannot invent — the profile's name and
 *    tagline, each section's heading, the opening line of each entry, and which
 *    column a slot lands in — comes from `GET /preview`. It still cannot know
 *    what the page will look like: the truthful check is "Build PDF", which
 *    returns the real page count and overfull-box count.
 *
 * 3. **The workspace is chosen, never guessed.** `GET /workspaces` names the
 *    workspaces under the Hermes home and the one the operator configured; a
 *    workspace anywhere else is addressed by PATH (the field below the picker).
 *    The door refuses to fall back to the server's working directory, so a pane
 *    that did not offer a path would be unable to use a workspace like this
 *    machine's own `templates.local/` tree.
 *
 * Written inside the desktop SDK surface: `jsx()` calls (the file is not
 * compiled), only `@hermes/plugin-sdk` / `react` / `react/jsx-runtime`
 * imported, no `eval`, no dynamic import, no app-DOM reaching. `scripts/
 * check_desktop_plugin.py` holds that line in CI.
 */

import {
  Badge,
  Button,
  EmptyState,
  GlyphSpinner,
  Input,
  PALETTE_AREA,
  PANES_AREA,
  SegmentedControl,
  Separator,
  Tip,
  cn,
  haptic,
  host
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useCallback, useEffect, useState } from 'react'

/** The contribution-scoped pane id: `<pluginId>:<paneId>`. `revealPane` wants it. */
const PANE_ID = 'evergreen:configurator'

/** Where the form's own state is remembered, so a reload does not lose it. */
const STORAGE_KEY = 'configurator.v1'

/** A template id is also a directory name — the same rule the backend enforces. */
const ID_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/

const WORKSPACE_HINT =
  'A workspace name, or the path of any directory that holds a career.yaml.'

// Font-size words as LaTeX's own scale, so a "size" knob's bar is proportional
// to something real rather than to an invented rank.
const SIZE_STEPS = {
  tiny: 0.6,
  scriptsize: 0.7,
  footnotesize: 0.8,
  small: 0.9,
  normalsize: 1,
  large: 1.2,
  Large: 1.44,
  LARGE: 1.73,
  huge: 2.07,
  Huge: 2.49,
  HUGE: 2.99,
  gigantic: 3.6,
  Gigantic: 4.3
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

function describeError(error) {
  if (!error) {
    return 'Unknown error.'
  }
  if (typeof error === 'string') {
    return error
  }
  // A FastAPI refusal carries its reason in `detail`, and the door's messages
  // are written to be read ("no route", "unknown setting, this template
  // offers…"). The bridge may hand it back on the error, on the parsed body, or
  // on a response wrapper, so all three are tried before the bare message.
  const detail = [error.detail, error.body && error.body.detail, error.response && error.response.detail]
    .find(value => typeof value === 'string' && value.trim())
  if (detail) {
    return detail
  }
  const message = error.message || String(error)
  const status = error.status || error.statusCode
  return status && !message.includes(String(status)) ? message + ' (HTTP ' + status + ')' : message
}

function query(workspace, template) {
  const parts = []
  const text = (workspace || '').trim()
  if (text) {
    parts.push('workspace=' + encodeURIComponent(text))
  }
  if (template) {
    parts.push('template=' + encodeURIComponent(template))
  }
  return parts.length > 0 ? '?' + parts.join('&') : ''
}

function savedState(ctx) {
  try {
    const stored = ctx.storage.get(STORAGE_KEY, null)
    return stored && typeof stored === 'object' ? stored : {}
  } catch {
    return {}
  }
}

/** Every knob at its default, then whatever the caller last chose for it. */
function valuesFor(recipe, previous) {
  const out = {}
  for (const knob of recipe.knobs || []) {
    const prior = previous ? previous[knob.id] : undefined
    out[knob.id] = prior === undefined || prior === null ? knob.default : prior
  }
  return out
}

function clamp(value, low, high) {
  const number = Number(value)
  if (!Number.isFinite(number)) {
    return low
  }
  return Math.min(high, Math.max(low, number))
}

function sizeStep(value) {
  // A size knob holds a LaTeX command (`\Huge`, `\footnotesize`), so the
  // backslash has to come off before the name is looked up — otherwise every
  // \command falls through to the neutral 1 and every bar comes out the same
  // length, which is a drawing that says nothing.
  const text = String(value ?? '').trim().replace(/^\\/, '')
  // Exact name first: the table distinguishes `large`/`Large`/`LARGE`, and a
  // case-insensitive lookup alone would answer \LARGE with large's step.
  if (text in SIZE_STEPS) {
    return SIZE_STEPS[text]
  }
  const key = Object.keys(SIZE_STEPS).find(name => name.toLowerCase() === text.toLowerCase())
  if (key) {
    return SIZE_STEPS[key]
  }
  const number = Number(text)
  return Number.isFinite(number) && number > 0 ? clamp(number, 0.5, 4) : 1
}

function isSizeKnob(knob) {
  return knob.type === 'enum' && /size|font/i.test(knob.id + ' ' + knob.label)
}

// ---------------------------------------------------------------------------
// The schematic preview
// ---------------------------------------------------------------------------

// Which role a colour knob plays on the page. A recipe's own description names
// the roles a colour drives ("also drives: body") — that is the generator's
// measurement, so it wins; failing that the id and label name it. Ordered, and
// the first match wins, because one word can fit two roles ("LightGrey" is a
// body colour, not a secondary one).
const COLOUR_ROLES = [
  ['panel', /wash|sidebar|panel/],
  ['page', /paper|page|background/],
  ['rule', /headingrule|rule|gold/],
  ['strong', /emphasis/],
  // `\bheading\b` on purpose: "subheading" is an accent role, and a looser
  // match made PastelRed claim the heading colour as well as the accent one.
  ['head', /\bheading\b|headline|namefont/],
  ['accent', /accent|subheading|tagline/],
  ['secondary', /soft|muted|secondary/],
  ['body', /body|ink|text|grey|gray|slate/]
]

function roleOf(knob) {
  const words = String(knob.description || '') + ' ' + knob.id + ' ' + String(knob.label || '')
  const text = words.toLowerCase()
  for (const [role, pattern] of COLOUR_ROLES) {
    if (pattern.test(text)) {
      return role
    }
  }
  return null
}

function hexOf(knob, values) {
  return '#' + String(values[knob.id] ?? knob.default).replace(/^#/, '')
}

/** role → the colour chosen for it, and which knob won it. */
function colourRoles(knobs, values) {
  const roles = {}
  const owners = {}
  for (const knob of knobs) {
    if (knob.type !== 'color') {
      continue
    }
    const role = roleOf(knob)
    if (role && !roles[role]) {
      roles[role] = hexOf(knob, values)
      owners[role] = knob.id
    }
  }
  return { roles, owners }
}

/** What to call a slot when there is no profile to name it. */
function slotTitle(slot) {
  const name = String(slot).startsWith('section:') ? String(slot).slice(8) : String(slot)
  return name.replace(/[-_]+/g, ' ').replace(/^./, letter => letter.toUpperCase())
}

/**
 * A page-shaped sketch: the header, one block per slot carrying that slot's
 * real heading and the opening of its entries, painted with the chosen
 * colours.
 *
 * Three facts make it specific rather than generic, and each comes from the
 * place that owns it: the wording and section titles from the workspace's own
 * profile (via `/preview`), which column a section lands in from the template's
 * own `\switchcolumn` (via `slot_columns`), and the colours from the knobs. It
 * is still labelled a sketch, because only a build shows the real page — and a
 * user who believes otherwise makes decisions on a drawing.
 */
function Schematic({ template, knobs, values, preview }) {
  const ratioKnob = knobs.find(
    knob => knob.type === 'number' && /ratio|split|column/i.test(knob.id + ' ' + knob.label)
  )
  const colours = knobs.filter(knob => knob.type === 'color')
  const sizes = knobs.filter(isSizeKnob)
  const { roles, owners } = colourRoles(knobs, values)
  // The page's own colours, not the app's: a CV is drawn on paper, and a
  // template that declares no page or body colour renders black on white. The
  // door sends those two as data, so nothing here hardcodes a colour, and a
  // knob that DOES declare them still wins.
  const defaults = (preview && preview.defaults) || {}
  const defaultHex = (key, fallback) =>
    defaults && typeof defaults[key] === 'string' && defaults[key]
      ? '#' + String(defaults[key]).replace(/^#/, '')
      : fallback
  const paper = roles.page || defaultHex('paper', 'var(--ui-bg-primary)')
  const ink = roles.body || defaultHex('ink', 'var(--ui-text-primary)')
  const head = roles.head || ink
  const strong = roles.strong || head
  const soft = roles.secondary || ink
  const accent = roles.accent || soft
  const panel = roles.panel || null
  // A template that declares no rule colour still has section rules; drawn in
  // the body colour at low weight rather than invented in a colour of its own.
  const rule = roles.rule || null

  const columns = Number((preview && preview.columns) || template.columns) || 1
  const twoColumn = columns > 1 || ratioKnob !== undefined
  const drawnRatio = ratioKnob ? Number(values[ratioKnob.id] ?? ratioKnob.default) : 0.6
  const ratio = clamp(drawnRatio, 0.2, 0.85)
  const ratioClamped = Number.isFinite(drawnRatio) && Math.abs(drawnRatio - ratio) > 0.0001

  const header = (preview && preview.header) || null
  const sections = preview && (preview.sections || []).length > 0
    ? preview.sections
    : (template.slots || [])
        .filter(slot => slot !== 'identity')
        .map(slot => ({ slot, title: slotTitle(slot), column: 'left', entries: [] }))

  // A size knob naming the name or heading font scales the sketch's header, so
  // "what does a bigger name look like" is answered here rather than later.
  const nameKnob = knobs.find(
    knob => isSizeKnob(knob) && /name|heading|title/i.test(knob.id + ' ' + knob.label)
  )
  const nameScale = nameKnob
    ? clamp(sizeStep(values[nameKnob.id] ?? nameKnob.default), 0.6, 1.6)
    : 1

  const bar = (width, key) =>
    jsx(
      'div',
      {
        style: {
          height: '2px',
          width,
          borderRadius: '1px',
          background: ink,
          opacity: 0.5
        }
      },
      key
    )

  const entryBlock = (entry, key) =>
    jsxs(
      'div',
      {
        style: { display: 'flex', flexDirection: 'column', gap: '1px' },
        children: [
          entry.headline
            ? jsx('div', {
                style: {
                  fontSize: '5.4px',
                  fontWeight: 600,
                  lineHeight: 1.25,
                  color: strong
                },
                children: entry.headline
              })
            : null,
          entry.meta
            ? jsx('div', {
                style: {
                  fontSize: '4.8px',
                  lineHeight: 1.2,
                  color: soft,
                },
                children: entry.meta
              })
            : null,
          entry.detail
            ? jsx('div', {
                style: {
                  fontSize: '4.8px',
                  lineHeight: 1.35,
                  color: ink,
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden'
                },
                children: entry.detail
              })
            : null,
          // The bullets the quoted line is the first of, drawn as bars: the
          // height an entry takes is a real fact about the page, and inventing
          // four lines of prose for a 5px font would be worse than a bar.
          ...Array.from({ length: Math.min(Number(entry.more) || 0, 2) }, (_, index) =>
            bar(['92%', '78%'][index] || '80%', key + ':more' + index)
          )
        ]
      },
      key
    )

  const sectionBlock = section => {
    const entries = section.entries || []
    return jsxs(
      'div',
      {
        style: { display: 'flex', flexDirection: 'column', gap: '2px' },
        children: [
          jsx('div', {
            style: {
              fontSize: '6px',
              fontWeight: 700,
              letterSpacing: '0.07em',
              textTransform: 'uppercase',
              lineHeight: 1.2,
              color: head,
            },
            children: section.title
          }),
          jsx('div', {
            style: {
              height: '1px',
              background: rule || ink,
              opacity: rule ? 0.9 : 0.3
            }
          }),
          jsx('div', {
            style: {
              display: 'flex',
              flexDirection: 'column',
              gap: '3px',
              marginTop: '1px'
            },
            children:
              entries.length > 0
                ? entries.map((entry, index) => entryBlock(entry, section.slot + ':' + index))
                : [
                    bar('82%', section.slot + ':b0'),
                    bar('64%', section.slot + ':b1'),
                    bar('71%', section.slot + ':b2')
                  ]
          })
        ]
      },
      section.slot
    )
  }

  const headerBlock = jsxs('div', {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: '2px',
      padding: '11px 13px 5px'
    },
    children: [
      jsx('div', {
        style: {
          fontSize: Math.round(12 * nameScale) + 'px',
          fontWeight: 700,
          lineHeight: 1.1,
          letterSpacing: '0.01em',
          color: head,
        },
        children: header && header.name ? header.name : 'Your Name'
      }),
      header && header.tagline
        ? jsx('div', {
            style: {
              fontSize: '5.6px',
              lineHeight: 1.3,
              color: accent,
            },
            children: header.tagline
          })
        : null,
      header && (header.contact || []).length > 0
        ? jsx('div', {
            style: {
              fontSize: '5px',
              lineHeight: 1.3,
              color: soft,
            },
            children: header.contact.join('   ·   ')
          })
        : null,
      jsx('div', {
        style: {
          height: '1.5px',
          marginTop: '4px',
          background: rule || ink,
          opacity: rule ? 0.9 : 0.3
        }
      })
    ]
  })

  const columnOf = side =>
    sections
      .filter(section => (section.column || 'left') === side)
      .map(section => sectionBlock(section))

  const page = jsxs('div', {
    style: {
      display: 'flex',
      flexDirection: 'column',
      aspectRatio: '8.5 / 11',
      width: '100%',
      border: '1px solid var(--ui-stroke-secondary)',
      borderRadius: '3px',
      overflow: 'hidden',
      background: paper,
      color: ink
    },
    children: [
      headerBlock,
      twoColumn
        ? jsxs('div', {
            style: {
              display: 'flex',
              flex: 1,
              minHeight: 0,
              gap: '8px',
              padding: '5px 12px 12px'
            },
            children: [
              jsx('div', {
                style: {
                  width: Math.round(ratio * 100) + '%',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '7px',
                  padding: '7px 6px',
                  borderRadius: '2px',
                  background: panel || 'transparent',
                  overflow: 'hidden'
                },
                children: columnOf('left')
              }),
              jsx('div', {
                style: {
                  flex: 1,
                  minWidth: 0,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '7px',
                  overflow: 'hidden'
                },
                children: columnOf('right')
              })
            ]
          })
        : jsx('div', {
            style: {
              display: 'flex',
              flex: 1,
              minHeight: 0,
              flexDirection: 'column',
              gap: '7px',
              padding: '5px 13px 12px',
              overflow: 'hidden'
            },
            children: columnOf('left')
          })
    ]
  })

  const workspaceName =
    preview && preview.workspace && preview.workspace.name ? preview.workspace.name : null
  const splitText = twoColumn
    ? `the split is drawn at ${Math.round(ratio * 100)}% / ${Math.round((1 - ratio) * 100)}%` +
      (ratioClamped ? ` (the knob says ${String(drawnRatio)}, so it is clamped)` : '')
    : 'one column, no split to draw'
  const wordingText = workspaceName
    ? 'sections and wording from ' + workspaceName
    : 'placeholder lines, because no profile could be read'

  return jsxs('div', {
    className: 'flex flex-col gap-2',
    children: [
      page,
      jsx('div', {
        className: 'text-[0.6875rem] leading-4 text-(--ui-text-quaternary)',
        children: `Schematic, not a render: ${splitText}; ${wordingText}.`
      }),
      preview && preview.wording_error
        ? jsx('div', {
            className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)',
            children: 'No wording: ' + preview.wording_error
          })
        : null,
      colours.length > 0
        ? jsx('div', {
            className: 'flex flex-wrap items-center gap-1.5',
            children: colours.map(knob => {
              const role = roleOf(knob)
              // Only the knob that actually paints the role is labelled with
              // it: two colours can name the same role (minimal's RuleGold and
              // GoldenEarth both read as a rule), and a legend that claims a
              // role for a colour the drawing never uses is a lie about the
              // page.
              const painted = role && owners[role] === knob.id ? role : null
              return jsxs(
                'span',
                {
                  className: 'inline-flex items-center gap-1 text-[0.6875rem] text-(--ui-text-tertiary)',
                  children: [
                    jsx('span', {
                      className: 'size-3 rounded-[2px] border border-(--ui-stroke-secondary)',
                      style: { backgroundColor: hexOf(knob, values) }
                    }),
                    jsx('span', { children: knob.label }),
                    painted
                      ? jsx('span', {
                          className: 'text-(--ui-text-quaternary)',
                          children: painted
                        })
                      : null
                  ]
                },
                knob.id
              )
            })
          })
        : null,
      sizes.length > 0
        ? jsx('div', {
            className: 'flex flex-col gap-1',
            children: sizes.map(knob =>
              jsxs(
                'div',
                {
                  className: 'flex items-center gap-2',
                  children: [
                    jsx('span', {
                      className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                      style: { width: '6.5rem' },
                      children: knob.label
                    }),
                    jsx('span', {
                      className: 'inline-block rounded-[1px] bg-(--ui-accent)',
                      style: {
                        height: '3px',
                        width: Math.round(clamp(sizeStep(values[knob.id] ?? knob.default) / 3, 0.15, 1) * 100) + '%',
                        opacity: 0.75
                      }
                    })
                  ]
                },
                knob.id
              )
            )
          })
        : null
    ]
  })
}

// ---------------------------------------------------------------------------
// One knob's control
// ---------------------------------------------------------------------------

function KnobRow({ knob, value, onChange, disabled }) {
  const label = jsxs('div', {
    className: 'flex items-baseline justify-between gap-2',
    children: [
      jsx('span', { className: 'text-xs font-medium text-(--ui-text-primary)', children: knob.label }),
      jsx('span', { className: 'text-[0.625rem] text-(--ui-text-quaternary)', children: knob.type })
    ]
  })

  let control = null
  if (knob.type === 'enum') {
    const options = knob.options || []
    control = jsx(SegmentedControl, {
      disabled,
      sizing: 'content',
      value: String(value ?? knob.default),
      options: options.map(option => ({ id: option, label: option })),
      onChange: next => onChange(next)
    })
  } else if (knob.type === 'color') {
    const hex = String(value ?? knob.default).replace(/^#/, '')
    control = jsxs('div', {
      className: 'flex items-center gap-2',
      children: [
        jsx('span', {
          className: 'size-6 shrink-0 rounded-[2px] border border-(--ui-stroke-secondary)',
          style: { backgroundColor: '#' + hex }
        }),
        jsx(Input, {
          disabled,
          spellCheck: false,
          value: hex,
          placeholder: '8F0D0D',
          onChange: event => onChange(event.target.value)
        })
      ]
    })
  } else if (knob.type === 'number') {
    control = jsx(Input, {
      disabled,
      type: 'number',
      step: 'any',
      min: knob.min,
      max: knob.max,
      value: String(value ?? knob.default),
      onChange: event => onChange(event.target.value)
    })
  } else {
    control = jsx(Input, {
      disabled,
      spellCheck: false,
      value: String(value ?? knob.default),
      placeholder: knob.type === 'length' ? '0.75in' : '',
      onChange: event => onChange(event.target.value)
    })
  }

  return jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      label,
      control,
      knob.description
        ? jsx('div', {
            className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)',
            children: knob.description
          })
        : null
    ]
  })
}

// ---------------------------------------------------------------------------
// The pane
// ---------------------------------------------------------------------------

function ConfiguratorPane({ ctx }) {
  const [restored] = useState(() => savedState(ctx))

  const [catalog, setCatalog] = useState(null)
  const [workspaces, setWorkspaces] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)

  const [workspace, setWorkspace] = useState(String(restored.workspace || ''))
  const [base, setBase] = useState(String(restored.base || ''))
  const [values, setValues] = useState(
    restored.values && typeof restored.values === 'object' ? restored.values : {}
  )
  const [newId, setNewId] = useState(String(restored.newId || ''))
  const [newName, setNewName] = useState(String(restored.newName || ''))

  const [busy, setBusy] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [generated, setGenerated] = useState(restored.generated || null)
  const [built, setBuilt] = useState(null)

  // What the sketch draws: the workspace's own headings and opening lines, and
  // the template's column assignment. Held apart from the catalog because it is
  // per-template wording, not per-template settings, and it is refreshed by the
  // same Reload that re-reads the catalog.
  const [preview, setPreview] = useState(null)
  const [previewNonce, setPreviewNonce] = useState(0)

  // Remember the form across a reload, a hot edit, or a pane close. This is the
  // "the settings survive a round trip" property: the pane reopens showing what
  // was typed, not the template's defaults.
  useEffect(() => {
    try {
      ctx.storage.set(STORAGE_KEY, { workspace, base, values, newId, newName, generated })
    } catch {
      // Storage is a convenience; losing it must never break the form.
    }
  }, [ctx, workspace, base, values, newId, newName, generated])

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const templates = await ctx.rest('/templates' + query(workspace))
      setCatalog(templates)
      setLoadError(templates.workspace_error || null)
    } catch (error) {
      setCatalog(null)
      setLoadError(describeError(error))
    } finally {
      setLoading(false)
    }

    // The picker is a convenience; the templates call above reports the real
    // failure, so a workspaces request that fails does not blank the pane.
    try {
      const known = await ctx.rest('/workspaces')
      setWorkspaces(known.workspaces || [])
    } catch {
      setWorkspaces([])
    }
  }, [ctx, workspace])

  useEffect(() => {
    reload()
  }, [reload])

  const bases = (catalog && catalog.templates ? catalog.templates : []).filter(row => row.generate_from)
  const selected = bases.find(row => row.id === base) || bases[0] || null
  const effective = selected ? valuesFor(selected.recipe, values) : {}
  const tectonic = catalog ? catalog.tectonic : null
  const selectedId = selected ? selected.id : ''

  // The sketch's wording is per (workspace, template), not per setting: typing a
  // colour must not cost a round trip, but picking another base or workspace
  // must. A failed preview is recorded rather than raised — a sketch with
  // placeholder lines is still worth drawing, and the form must not blank.
  useEffect(() => {
    if (!selectedId) {
      setPreview(null)
      return undefined
    }
    let cancelled = false
    setPreview(null)
    ctx
      .rest('/preview' + query(workspace, selectedId))
      .then(data => {
        if (!cancelled) {
          setPreview(data)
        }
      })
      .catch(error => {
        if (!cancelled) {
          setPreview({ ok: false, header: null, sections: [], wording_error: describeError(error) })
        }
      })
    return () => {
      cancelled = true
    }
  }, [ctx, workspace, selectedId, previewNonce])

  const chooseBase = id => {
    setBase(id)
    setBuilt(null)
    setActionError(null)
  }

  const setValue = (knobId, next) => {
    setValues(current => ({ ...current, [knobId]: next }))
  }

  const trimmedId = newId.trim()
  const idOk = ID_RE.test(trimmedId)
  const canGenerate = Boolean(selected) && idOk && busy === null

  const generate = async () => {
    if (!selected) {
      return
    }
    setBusy('generate')
    setActionError(null)
    setBuilt(null)
    try {
      const reply = await ctx.rest('/generate', {
        method: 'POST',
        body: {
          base: selected.id,
          id: trimmedId,
          name: newName.trim(),
          workspace: workspace.trim() || null,
          settings: effective
        }
      })
      setGenerated(reply)
      haptic('tap')
      host.notify({
        kind: 'success',
        message: 'Generated ' + reply.id + ' in templates.local/.',
        detail: reply.generated
      })
    } catch (error) {
      setGenerated(null)
      setActionError(describeError(error))
    } finally {
      setBusy(null)
    }
  }

  const build = async templateId => {
    setBusy('build')
    setActionError(null)
    try {
      // A tectonic build is seconds to a minute; the door runs it off the event
      // loop, so the only thing at risk is this request's patience.
      const reply = await ctx.rest('/build', {
        method: 'POST',
        body: { template: templateId, workspace: workspace.trim() || null },
        timeoutMs: 180000
      })
      setBuilt(reply)
      haptic('tap')
    } catch (error) {
      setBuilt(null)
      setActionError(describeError(error))
    } finally {
      setBusy(null)
    }
  }

  // -- render -------------------------------------------------------------

  const header = jsxs('div', {
    className: 'flex items-center justify-between gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
    children: [
      jsx('div', {
        className: 'text-xs font-medium text-(--ui-text-primary)',
        children: 'Configurator'
      }),
      jsx(Button, {
        variant: 'ghost',
        size: 'xs',
        disabled: loading || busy !== null,
        onClick: () => {
          reload()
          setPreviewNonce(current => current + 1)
        },
        children: 'Reload'
      })
    ]
  })

  if (loading && !catalog) {
    return jsxs('div', {
      className: 'flex h-full flex-col',
      children: [
        header,
        jsxs('div', {
          className: 'flex flex-1 items-center justify-center gap-2 text-xs text-(--ui-text-tertiary)',
          children: [jsx(GlyphSpinner, {}), jsx('span', { children: 'Reading templates…' })]
        })
      ]
    })
  }

  const workspaceField = jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      jsx('div', { className: 'text-xs font-medium text-(--ui-text-primary)', children: 'Workspace' }),
      workspaces.length > 0
        ? jsx(SegmentedControl, {
            sizing: 'content',
            value: workspaces.some(entry => entry.name === workspace) ? workspace : '',
            options: workspaces.map(entry => ({ id: entry.name, label: entry.name })),
            onChange: name => {
              setWorkspace(name)
              setBuilt(null)
            }
          })
        : null,
      jsx(Input, {
        spellCheck: false,
        value: workspace,
        placeholder: 'name, or a directory holding career.yaml',
        onChange: event => {
          setWorkspace(event.target.value)
          setBuilt(null)
        }
      }),
      jsx('div', {
        className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)',
        children: WORKSPACE_HINT
      })
    ]
  })

  const baseList =
    bases.length > 0
      ? jsx('div', {
          className: 'flex flex-col gap-1',
          children: bases.map(row =>
            jsx(
              'button',
              {
                type: 'button',
                onClick: () => chooseBase(row.id),
                className: cn(
                  'rounded-[3px] px-2 py-1.5 text-left',
                  'border border-transparent',
                  selected && selected.id === row.id
                    ? 'border-(--ui-stroke-secondary) bg-(--ui-bg-quaternary)'
                    : 'hover:bg-(--chrome-action-hover)'
                ),
                children: jsxs('div', {
                  className: 'flex flex-col gap-0.5',
                  children: [
                    jsx('div', {
                      className: 'text-xs font-medium text-(--ui-text-primary)',
                      children: row.name || row.id
                    }),
                    jsx('div', {
                      className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                      children:
                        row.id +
                        ' · ' +
                        String(row.dialect || '?') +
                        ' · ' +
                        String(row.columns || 1) +
                        ' col · ' +
                        String(row.source || '')
                    })
                  ]
                })
              },
              row.id
            )
          )
        })
      : null

  // Why there is no base to configure is worth saying precisely: the plugin
  // ships no recipe (a recipe names real lines in a template, so it belongs to
  // whoever owns that template), so a fresh install legitimately has none.
  const noBase = jsx(EmptyState, {
    title: bases.length > 0 ? 'Nothing to configure yet' : 'No template with a recipe',
    description:
      bases.length > 0
        ? 'Pick a base template above.'
        : 'A template becomes configurable once it carries a recipe.yaml. Author one ' +
          'with `hermes career author <template> --path <template dir>`, or drop a ' +
          'recipe.yaml beside a template in templates.local/.'
  })

  const brokenRecipes = catalog
    ? catalog.templates.filter(row => row.recipe_error)
    : []

  const settingsBlock =
    selected && selected.recipe
      ? jsxs('div', {
          className: 'flex flex-col gap-2',
          children: [
            jsx('div', {
              className: 'text-xs font-medium text-(--ui-text-primary)',
              children: 'Settings'
            }),
            jsx('div', {
              className: 'flex flex-col gap-3',
              children: (selected.recipe.knobs || []).map(knob =>
                jsx(
                  KnobRow,
                  {
                    knob,
                    value: effective[knob.id],
                    disabled: busy !== null,
                    onChange: next => setValue(knob.id, next)
                  },
                  knob.id
                )
              )
            }),
            jsx(Separator, { className: 'my-1' }),
            jsx(Schematic, {
              template: selected,
              knobs: selected.recipe.knobs || [],
              values: effective,
              preview
            })
          ]
        })
      : null

  const generateBlock = selected
    ? jsxs('div', {
        className: 'flex flex-col gap-2',
        children: [
          jsx('div', {
            className: 'text-xs font-medium text-(--ui-text-primary)',
            children: 'Generate'
          }),
          jsx(Input, {
            spellCheck: false,
            value: newId,
            placeholder: 'new template id, e.g. my-cv',
            disabled: busy !== null,
            onChange: event => setNewId(event.target.value)
          }),
          jsx(Input, {
            spellCheck: false,
            value: newName,
            placeholder: 'name shown in the picker (optional)',
            disabled: busy !== null,
            onChange: event => setNewName(event.target.value)
          }),
          trimmedId && !idOk
            ? jsx('div', {
                className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)',
                children: 'An id is lowercase letters, digits, dot, dash or underscore, and becomes the directory name.'
              })
            : null,
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx(Button, {
                variant: 'default',
                size: 'sm',
                disabled: !canGenerate,
                onClick: () => generate(),
                children: busy === 'generate' ? 'Writing…' : 'Generate template'
              }),
              jsx(Tip, {
                label: 'Writes templates.local/<id>/ and leaves the base template untouched.',
                children: jsx('span', {
                  className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
                  children: 'writes a new directory'
                })
              })
            ]
          })
        ]
      })
    : null

  const generatedBlock = generated
    ? jsxs('div', {
        className: 'flex flex-col gap-2 rounded-[3px] border border-(--ui-stroke-secondary) p-2',
        children: [
          jsxs('div', {
            className: 'flex items-center justify-between gap-2',
            children: [
              jsxs('div', {
                className: 'flex items-center gap-1.5',
                children: [
                  jsx(Badge, { variant: 'muted', children: 'generated' }),
                  jsx('span', {
                    className: 'text-xs font-medium text-(--ui-text-primary)',
                    children: generated.id
                  })
                ]
              }),
              jsx(Button, {
                variant: 'ghost',
                size: 'xs',
                onClick: () => ctx.os.revealPath(generated.paths.dir),
                children: 'Reveal'
              })
            ]
          }),
          jsx('div', {
            className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)',
            children: generated.generated
          }),
          jsx('div', {
            className: 'flex flex-col gap-0.5',
            children: (generated.resolved || []).map(entry =>
              jsxs(
                'div',
                {
                  className: 'flex items-baseline justify-between gap-2 text-[0.6875rem]',
                  children: [
                    jsx('span', {
                      className: cn(entry.changed ? 'text-(--ui-text-primary)' : 'text-(--ui-text-quaternary)'),
                      children: entry.label
                    }),
                    jsx('span', {
                      className: 'font-mono text-(--ui-text-tertiary)',
                      children: entry.value
                    })
                  ]
                },
                entry.knob
              )
            )
          }),
          jsx(Button, {
            variant: 'secondary',
            size: 'sm',
            disabled: busy !== null || !tectonic,
            onClick: () => build(generated.id),
            children: busy === 'build' ? 'Building…' : 'Build PDF'
          })
        ]
      })
    : null

  const builtBlock = built
    ? jsxs('div', {
        className: 'flex flex-col gap-1 rounded-[3px] border border-(--ui-stroke-secondary) p-2',
        children: [
          jsxs('div', {
            className: 'flex items-center justify-between gap-2',
            children: [
              jsx('div', {
                className: 'text-xs font-medium text-(--ui-text-primary)',
                children:
                  String(built.pages) +
                  (built.pages === 1 ? ' page' : ' pages') +
                  ' · ' +
                  String(built.overfull_boxes) +
                  ' overfull'
              }),
              jsx(Button, {
                variant: 'ghost',
                size: 'xs',
                onClick: () => ctx.os.revealPath(built.pdf),
                children: 'Reveal PDF'
              })
            ]
          }),
          jsx('div', {
            className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)',
            children: built.pdf
          }),
          built.overfull_boxes > 0
            ? jsx('div', {
                className: 'text-[0.6875rem] leading-4 text-(--ui-text-secondary)',
                children:
                  'An overfull box means a line runs past its column; the PDF is real, but the layout is not clean.'
              })
            : null,
          (built.warnings || []).length > 0
            ? jsx('div', {
                className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)',
                children: built.warnings.join(' ')
              })
            : null
        ]
      })
    : null

  const actionErrorBlock = actionError
    ? jsxs('div', {
        className: 'flex flex-col gap-1 rounded-[3px] border border-(--ui-stroke-secondary) p-2',
        children: [
          jsx('div', {
            className: 'text-xs font-medium text-(--ui-text-primary)',
            children: 'That did not work'
          }),
          jsx('div', {
            className: 'text-[0.6875rem] leading-4 whitespace-pre-wrap text-(--ui-text-secondary)',
            children: actionError
          })
        ]
      })
    : null

  const loadErrorBlock = loadError
    ? jsxs('div', {
        className: 'flex flex-col gap-1 rounded-[3px] border border-(--ui-stroke-secondary) p-2',
        children: [
          jsx('div', {
            className: 'text-xs font-medium text-(--ui-text-primary)',
            children: 'No workspace'
          }),
          jsx('div', {
            className: 'text-[0.6875rem] leading-4 whitespace-pre-wrap text-(--ui-text-secondary)',
            children: loadError
          })
        ]
      })
    : null

  const tectonicBlock = catalog && !tectonic
    ? jsx('div', {
        className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)',
        children:
          'tectonic was not found, so "Build PDF" is off. Install it and reload: the door reports the install hint too.'
      })
    : null

  return jsxs('div', {
    className: 'flex h-full flex-col',
    children: [
      header,
      jsx('div', {
        className: 'flex-1 overflow-y-auto',
        children: jsxs('div', {
          className: 'flex flex-col gap-3 p-3',
          children: [
            workspaceField,
            loadErrorBlock,
            bases.length === 0 ? noBase : baseList,
            brokenRecipes.length > 0
              ? jsx('div', {
                  className: 'flex flex-col gap-1',
                  children: brokenRecipes.map(row =>
                    jsx(
                      'div',
                      {
                        className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)',
                        children: row.id + ': ' + row.recipe_error
                      },
                      row.id
                    )
                  )
                })
              : null,
            settingsBlock,
            generateBlock,
            generatedBlock,
            builtBlock,
            actionErrorBlock,
            tectonicBlock
          ]
        })
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

export default {
  id: 'evergreen',
  name: 'Evergreen Configurator',

  // A desktop half ships opt-in: it inventories in Capabilities → Plugins and
  // stays dark until the user flips the switch. Dropping the package in
  // `plugins/` must change nothing they can see.
  defaultEnabled: false,

  register(ctx) {
    ctx.register({
      id: 'configurator',
      area: PANES_AREA,
      title: 'Configurator',
      data: { placement: 'right', width: '420px' },
      render: () => jsx(ConfiguratorPane, { ctx })
    })

    ctx.register({
      id: 'open-configurator',
      area: PALETTE_AREA,
      data: {
        id: 'evergreen.configurator',
        label: 'Configurator',
        keywords: ['cv', 'resume', 'template', 'recipe', 'evergreen', 'latex'],
        run: () => {
          // Feature-detected: older desktops have no revealPane, and a pane
          // contribution there is reachable by its own tab.
          if (typeof host.revealPane === 'function') {
            host.revealPane(PANE_ID)
          } else {
            host.notify({
              kind: 'info',
              message: 'This Desktop build cannot focus a pane from a command; open it from the layout.'
            })
          }
        }
      }
    })
  }
}
