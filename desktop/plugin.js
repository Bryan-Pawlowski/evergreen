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
 *    colour knob, a bar per size knob), so it updates with no round trip. It
 *    cannot know what the page will look like — the truthful check is "Build
 *    PDF", which returns the real page count and overfull-box count.
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

function query(workspace) {
  const text = (workspace || '').trim()
  return text ? '?workspace=' + encodeURIComponent(text) : ''
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
  const text = String(value ?? '').trim()
  if (text in SIZE_STEPS) {
    return SIZE_STEPS[text]
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

/**
 * A sketch of the page: the column split at the chosen ratio, a swatch per
 * colour knob, a bar per size knob, and the slots the template fills.
 *
 * It is labelled as a schematic in the UI. Claiming otherwise would be a lie a
 * user acts on — the real answer comes from a build.
 */
function Schematic({ template, knobs, values }) {
  const ratioKnob = knobs.find(
    knob => knob.type === 'number' && /ratio|split|column/i.test(knob.id + ' ' + knob.label)
  )
  const colours = knobs.filter(knob => knob.type === 'color')
  const sizes = knobs.filter(isSizeKnob)

  const columns = Number(template.columns) || 1
  const twoColumn = columns > 1 || ratioKnob !== undefined
  const ratio = ratioKnob
    ? clamp(values[ratioKnob.id] ?? ratioKnob.default, 0.2, 0.85)
    : 0.6

  const fill = index =>
    jsx(
      'div',
      {
        className: 'rounded-[1px] bg-(--ui-stroke-secondary)',
        style: { height: '2px', opacity: 0.55 }
      },
      index
    )

  const column = (count, label) =>
    jsxs(
      'div',
      {
        className: 'flex flex-col gap-[5px]',
        children: Array.from({ length: count }, (_, index) => fill(index))
      },
      label
    )

  return jsxs('div', {
    className: 'flex flex-col gap-2',
    children: [
      jsx('div', {
        className: 'rounded-[3px] border border-(--ui-stroke-secondary) p-2',
        style: { aspectRatio: '8.5 / 11' },
        children: twoColumn
          ? jsxs('div', {
              className: 'flex h-full gap-2',
              children: [
                jsx('div', {
                  className: 'rounded-[1px] bg-(--ui-bg-tertiary) p-1.5',
                  style: { width: Math.round(ratio * 100) + '%' },
                  children: column(10, 'left')
                }),
                jsx('div', { className: 'flex-1 p-1', children: column(14, 'right') })
              ]
            })
          : jsx('div', { className: 'h-full p-1', children: column(16, 'single') })
      }),
      jsx('div', {
        className: 'text-[0.6875rem] leading-4 text-(--ui-text-quaternary)',
        children: twoColumn
          ? `Schematic, not a render: the split is drawn at ${Math.round(ratio * 100)}% / ${Math.round((1 - ratio) * 100)}%.`
          : 'Schematic, not a render: one column, no split to draw.'
      }),
      colours.length > 0
        ? jsx('div', {
            className: 'flex flex-wrap items-center gap-1.5',
            children: colours.map(knob =>
              jsxs(
                'span',
                {
                  className: 'inline-flex items-center gap-1 text-[0.6875rem] text-(--ui-text-tertiary)',
                  children: [
                    jsx('span', {
                      className: 'size-3 rounded-[2px] border border-(--ui-stroke-secondary)',
                      style: { backgroundColor: '#' + String(values[knob.id] ?? knob.default).replace(/^#/, '') }
                    }),
                    jsx('span', { children: knob.label })
                  ]
                },
                knob.id
              )
            )
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
        : null,
      (template.slots || []).length > 0
        ? jsx('div', {
            className: 'flex flex-wrap gap-1',
            children: template.slots.map(slot =>
              jsx(
                Badge,
                {
                  variant: 'outline',
                  className: 'text-[0.625rem] leading-4',
                  children: slot
                },
                slot
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
        onClick: () => reload(),
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
            jsx(Schematic, { template: selected, knobs: selected.recipe.knobs || [], values: effective })
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
