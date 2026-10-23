"""Evergreen — a career workspace and LaTeX CV builder for Hermes.

The plugin gives an agent a *workspace* to hold one person's career data
(``career.yaml`` as the structured source of truth, markdown notes for prose
nuance) and renders that data into a LaTeX CV, tailored to a job description.

Tools:
    career_init       create a workspace at a chosen path
    career_ingest     store a source document in the workspace's raw layer
    career_profile    read or update the structured profile
    career_match      match a job description against the profile
    career_build      render and compile a CV
    career_templates  list the available templates
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from . import dialects
from . import matcher as matcher_mod
from . import workspace as ws_mod
from .build import build_tex, find_tectonic
from .build import BuildError, tectonic_install_hint  # noqa: F401  (re-exported)
from .career import (CareerProfile, CareerProfileError, Workspace,
                      _hermes_home_markers, find_workspace, hermes_home,
                      list_workspaces, validate, validate_workspace_name,
                      workspace_path_for_name)
from .generate import GenerateError, generate_template  # noqa: F401  (re-exported)
from .liveness import check_knobs, keep_live  # noqa: F401  (re-exported)
from .propose import (inline_spacing_overrides, propose,  # noqa: F401  (re-exported)
                      propose_recipe)
from .recipe import (Recipe, RecipeError, apply_settings,  # noqa: F401  (re-exported)
                     load_recipe, write_recipe)
from .render import (TemplateError, discover_templates, render_tex, slots_in, stage)
from .render import load_template  # noqa: F401  (re-exported)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_TEMPLATES = PLUGIN_ROOT / "templates"

# Where a workspace lives when the user has not chosen one. The config value
# is read from plugins.entries.<id>.settings.workspace_path.
_DEFAULT_WORKSPACE_NAME = "evergreen"  # suggested name for a first workspace


def _setting(ctx, key: str, default=None):
    try:
        return ctx.get_config(key, default)
    except Exception:
        return default


def _resolve_workspace(ctx, explicit: str | None = None,
                       name: str | None = None) -> Workspace:
    """Find the workspace to act on.

    Resolution order: an explicit name, an explicit path, the configured
    ``workspace_path``, then a walk up from the working directory. The
    name is the primary handle — a workspace is created under a name and
    referred to by that name.
    """
    if name:
        try:
            return Workspace(workspace_path_for_name(name))
        except CareerProfileError:
            # Fall through to the other resolution paths rather than
            # failing outright; the caller reports the real problem.
            pass

    if explicit:
        return Workspace(Path(explicit).expanduser())

    configured = _setting(ctx, "workspace_path")
    if configured:
        return Workspace(Path(str(configured)).expanduser())

    found = find_workspace()
    if found:
        return found

    # Fall back to the named workspaces under the Hermes home. If there is
    # exactly one, it is the obvious default; if there are several, the
    # caller should ask which one by name rather than guessing.
    workspaces = list_workspaces()
    if len(workspaces) == 1:
        return Workspace(Path(workspaces[0]["path"]))
    if workspaces:
        return Workspace(hermes_home() / workspaces[0]["name"])

    # Nothing exists yet. Point at the Hermes home so the error message
    # names the place a workspace would go.
    return Workspace(hermes_home())


def _shipped_template_roots(workspace: Workspace) -> list[Path]:
    """Template roots, later ones overriding earlier ones on an id collision."""
    return [SHIPPED_TEMPLATES, workspace.local_templates_dir]


def _resolve_template(workspace: Workspace, template_id: str | None):
    templates = discover_templates(*_shipped_template_roots(workspace))
    if not templates:
        raise TemplateError(
            f"No templates found. Expected them under {SHIPPED_TEMPLATES}."
        )
    if not template_id:
        return templates[0]
    for t in templates:
        if t.id == template_id:
            return t
    names = ", ".join(t.id for t in templates)
    raise TemplateError(f"No template {template_id!r}. Available: {names}.")


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

def career_init(params: dict, ctx=None, **kw) -> str:
    """Create a named career workspace under the Hermes home."""
    raw_name = (params.get("name") or "").strip()
    if not raw_name:
        return json.dumps({
            "error": "A workspace name is required. Pick a short name for "
                     "this workspace — it becomes a directory directly under "
                     "the Hermes home, e.g. 'jane'.",
        })

    try:
        name = validate_workspace_name(raw_name)
    except CareerProfileError as exc:
        return json.dumps({"error": str(exc)})

    # A workspace lives directly under the Hermes home, one level deeper
    # than the Hermes home itself.
    root = hermes_home() / name
    workspace = Workspace(root)

    if workspace.exists():
        return json.dumps({
            "ok": True,
            "status": "exists",
            "name": name,
            "path": str(workspace.root),
            "career_profile": str(workspace.career_path),
            "message": f"A workspace named {name!r} already exists.",
        })

    # Refuse to clobber a file that is not a career profile. The name is
    # distinctive, but a directory may still hold an unrelated career.yaml
    # — and overwriting someone's file is not a recoverable mistake.
    if workspace.career_path.exists():
        return json.dumps({
            "error": f"{workspace.career_path} already exists and is not a "
                     f"career profile. Refusing to overwrite it. Choose a "
                     f"different name, or move that file aside.",
            "path": str(workspace.career_path),
        })

    # The workspace sits directly under the Hermes home, so it shares that
    # directory with Hermes' own state: plugins/, sessions/, skills/,
    # state.db, and friends. A name that collides with one of those would
    # either destroy live state or shadow a directory Hermes expects to
    # find. Checked against the real home rather than a hardcoded list, so
    # this stays correct as Hermes grows new top-level entries.
    if root.exists():
        return json.dumps({
            "error": f"{root} already exists and is not a career workspace. "
                     f"That name is in use by the Hermes home. Pick a "
                     f"different name for the workspace.",
            "path": str(root),
        })

    # Refuse to build a workspace inside a Hermes home. A Hermes home holds
    # its own profile.yaml, config, and state database; littering it with
    # notes/, raw/, and out/ is not a workspace, it is pollution.
    markers = _hermes_home_markers(workspace.root)
    if markers:
        return json.dumps({
            "error": f"{workspace.root} looks like a Hermes home "
                     f"(found: {', '.join(markers)}). A career workspace "
                     f"should be its own directory — pick one outside the "
                     f"Hermes home, for example ~/evergreen.",
            "path": str(workspace.root),
            "hermes_home_markers": markers,
        })

    workspace.create()
    if not workspace.career_path.exists():
        workspace.career_path.write_text(
            "schema_version: 1\n"
            f"name: {name}\n"
            "person:\n"
            "  name:\n"
            "    first: ''\n"
            "    last: ''\n"
            "  tagline: ''\n"
            "  summary: ''\n"
            "experience: []\n"
            "projects: []\n"
            "education: []\n"
            "skills: []\n"
            "milestones: []\n"
            "languages: []\n",
            encoding="utf-8",
        )

    if ctx is not None:
        try:
            ctx.set_config("workspace_path", str(workspace.root))
        except Exception:
            pass

    state = {item["key"]: {"done": False, "answer": None}
             for item in ws_mod.CHECKLIST}
    ws_mod.save_checklist(workspace, state)

    return json.dumps({
        "ok": True,
        "name": name,
        "path": str(workspace.root),
        "career_profile": str(workspace.career_path),
        "next_question": ws_mod.CHECKLIST[0]["prompt"],
        "layout": {
            "career_profile": "career.yaml (the structured source of truth)",
            "notes": "notes/ (prose nuance)",
            "raw": "raw/ (immutable ingested sources)",
            "targets": "targets/ (per-job match results)",
            "out": "out/ (rendered PDFs)",
            "templates.local": "templates.local/ (your own LaTeX templates)",
        },
    }, indent=2)


def career_list(params: dict, ctx=None, **kw) -> str:
    """List the career workspaces that live under the Hermes home."""
    workspaces = list_workspaces()
    return json.dumps({
        "ok": True,
        "hermes_home": str(hermes_home()),
        "count": len(workspaces),
        "workspaces": workspaces,
    }, indent=2)


def career_ingest(params: dict, ctx=None, **kw) -> str:
    """Store a source document in the workspace's immutable raw layer."""
    workspace = _resolve_workspace(ctx, params.get("workspace"),
                                   params.get("name"))
    text = (params.get("text") or "").strip()
    source = (params.get("source") or "pasted document").strip()

    if not text:
        return json.dumps({
            "error": "Nothing to ingest. Pass the document text in `text`, "
                     "or read the file yourself and pass its contents."
        })

    try:
        result = ws_mod.ingest_text(workspace, text, source,
                                    kind=params.get("kind") or "document")
    except OSError as exc:
        return json.dumps({"error": f"Could not write to the raw layer: {exc}"})

    return json.dumps({
        "ok": True,
        **result,
        "note": "The raw layer is immutable. To correct a source, ingest a new "
                "document rather than editing the stored one.",
    }, indent=2)


def _merge_by_id(existing: list, patch_items: list) -> list:
    """Merge two lists of profile entries by their ``id``.

    Used only when BOTH lists carry ids on every element. An entry in the
    patch whose id already exists is merged into it; an entry with a new id
    is appended. Existing entries keep their position, new ones go at the
    end.
    """
    result = [dict(e) if isinstance(e, dict) else e for e in existing]
    index = {
        e.get("id"): i
        for i, e in enumerate(result)
        if isinstance(e, dict) and e.get("id")
    }
    for item in patch_items:
        if not isinstance(item, dict):
            result.append(item)
            continue
        item_id = item.get("id")
        if item_id and item_id in index:
            position = index[item_id]
            merged = dict(result[position])
            merged.update(item)
            result[position] = merged
        else:
            if item_id:
                index[item_id] = len(result)
            result.append(dict(item))
    return result


def _merge_value(existing, patch):
    """Merge a patch value into an existing value.

    The rules, chosen so a patch means "change what I named":

    - **dict into dict** — merge key by key, recursively. So a patch of
      ``{"person": {"tagline": "x"}}`` changes the tagline and leaves the
      name, email, and links alone. Replacing the whole dict instead
      would drop required fields and make every patch a full rewrite.
    - **list into list** — merge by ``id`` when *both* lists carry an id on
      every element (the entries that have one: experience, projects,
      education, milestones, sections). Otherwise replace: a list without
      ids (skills, languages, links) is a value, not a set of records, so
      a patch to it is a replacement rather than an append.
    - **anything else** — replace.
    """
    if isinstance(existing, dict) and isinstance(patch, dict):
        merged = dict(existing)
        for key, value in patch.items():
            merged[key] = _merge_value(existing.get(key), value)
        return merged

    if isinstance(existing, list) and isinstance(patch, list):
        def all_have_ids(items: list) -> bool:
            return bool(items) and all(
                isinstance(i, dict) and i.get("id") for i in items
            )
        if all_have_ids(existing) and all_have_ids(patch):
            return _merge_by_id(existing, patch)
        return list(patch)

    return patch


def career_profile(params: dict, ctx=None, **kw) -> str:
    """Read the profile, or update it with a partial document."""
    workspace = _resolve_workspace(ctx, params.get("workspace"),
                                   params.get("name"))
    action = (params.get("action") or "read").strip().lower()

    try:
        profile = workspace.load()
    except CareerProfileError as exc:
        return json.dumps({"error": str(exc)})

    if action == "read":
        return json.dumps({
            "ok": True,
            "path": str(profile.path),
            "career_profile": profile.to_dict(),
        }, indent=2, ensure_ascii=False)

    if action != "update":
        return json.dumps({"error": f"Unknown action {action!r}. Use read or update."})

    patch = params.get("patch")
    if isinstance(patch, str):
        try:
            patch = json.loads(patch)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"`patch` is not valid JSON: {exc}"})
    if not isinstance(patch, dict):
        return json.dumps({
            "error": "`patch` must be a mapping of top-level profile fields, "
                     "for example {\"person\": {\"summary\": \"...\"}}."
        })

    data = profile.to_dict()
    for key, value in patch.items():
        data[key] = _merge_value(data.get(key), value)

    errors = validate(data)
    if errors:
        return json.dumps({
            "error": "The patch would make the profile invalid.",
            "details": errors,
        }, indent=2)

    try:
        workspace.save(CareerProfile(data, workspace.career_path))
    except CareerProfileError as exc:
        return json.dumps({"error": str(exc)})

    return json.dumps({
        "ok": True,
        "updated": list(patch.keys()),
        "path": str(workspace.career_path),
    }, indent=2)


def career_match(params: dict, ctx=None, **kw) -> str:
    """Match a job description against the profile and rank the entries."""
    workspace = _resolve_workspace(ctx, params.get("workspace"),
                                   params.get("name"))
    jd_text = (params.get("job_description") or "").strip()

    if not jd_text:
        jd_path = (params.get("job_description_path") or "").strip()
        if jd_path:
            try:
                jd_text = Path(jd_path).expanduser().read_text(encoding="utf-8")
            except OSError as exc:
                return json.dumps({"error": f"Could not read {jd_path}: {exc}"})

    if not jd_text:
        return json.dumps({
            "error": "A job description is required. Pass its text in "
                     "`job_description`, or a path in `job_description_path`."
        })

    try:
        profile = workspace.load()
    except CareerProfileError as exc:
        return json.dumps({"error": str(exc)})

    report = matcher_mod.match(profile, jd_text)

    # Persist the report so the render step and a later session agree on it.
    workspace.targets_dir.mkdir(parents=True, exist_ok=True)
    target = (params.get("target") or "untitled").strip()
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in target).strip("-")
    out_path = workspace.targets_dir / f"{safe or 'untitled'}.json"
    out_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return json.dumps({
        "ok": True,
        "target": safe or "untitled",
        "saved": str(out_path),
        "report": report.to_dict(),
        "guidance": "Selection is deterministic. The next step rewrites the "
                    "wording of the bullets that already speak to the job; it "
                    "does not add new claims.",
    }, indent=2, ensure_ascii=False)


def career_build(params: dict, ctx=None, **kw) -> str:
    """Render the profile into a LaTeX CV and compile it to PDF."""
    workspace = _resolve_workspace(ctx, params.get("workspace"),
                                   params.get("name"))

    try:
        profile = workspace.load()
    except CareerProfileError as exc:
        return json.dumps({"error": str(exc)})

    try:
        template = _resolve_template(workspace, params.get("template"))
    except TemplateError as exc:
        return json.dumps({"error": str(exc)})

    selection = None
    target = (params.get("target") or "").strip()
    if target:
        report_path = workspace.targets_dir / f"{target}.json"
        if not report_path.exists():
            return json.dumps({
                "error": f"No match report for target {target!r}. "
                         f"Run career_match first, or omit `target`."
            })
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return json.dumps({"error": f"Could not read the match report: {exc}"})
        by_slot = report.get("by_slot") or {}
        selection = {}
        for slot, items in by_slot.items():
            entries = []
            for item in items:
                entry = item.get("entry")
                if isinstance(entry, dict):
                    entries.append(entry)
            if entries:
                selection[slot] = entries
        # A tailored CV still needs the header, the summary, and the links —
        # the matcher only ranks the career entries. Slots the report does
        # not cover fall back to the profile's visible entries.
        for slot in ("identity", "summary", "links", "languages"):
            selection.setdefault(slot, profile.visible(slot))

    try:
        tex = render_tex(template, profile, selection)
    except TemplateError as exc:
        return json.dumps({"error": str(exc)})

    workdir = Path(tempfile.mkdtemp(prefix="evergreen-build-"))
    result = None
    final_pdf = None
    try:
        main = stage(template, workdir)
        main.write_text(tex, encoding="utf-8", newline="\n")

        fontconfig = None
        if template.fontconfig and (workdir / template.fontconfig).exists():
            fontconfig = str(workdir / template.fontconfig)

        result = build_tex(main, workdir, engine=template.engine,
                           fontconfig=fontconfig)

        # Copy the artifacts OUT before the workdir is removed — the PDF and
        # the log live in the workdir, and `finally` deletes it.
        if result.ok:
            workspace.out_dir.mkdir(parents=True, exist_ok=True)
            final_pdf = workspace.out_dir / f"{target or 'cv'}-{template.id}.pdf"
            shutil.copy2(result.pdf, final_pdf)
    finally:
        if not params.get("keep_workdir"):
            shutil.rmtree(workdir, ignore_errors=True)

    if not result.ok:
        return json.dumps({
            "ok": False,
            "template": template.id,
            **result.to_dict(),
        }, indent=2)

    # Keep the .tex next to the PDF: a rendered CV is a view, and the source
    # is what you edit.
    tex_path = workspace.out_dir / f"{target or 'cv'}-{template.id}.tex"
    tex_path.write_text(tex, encoding="utf-8", newline="\n")

    payload = {
        "ok": True,
        "template": template.id,
        "slots": slots_in(template),
        "pdf": str(final_pdf),
        "tex": str(tex_path),
        "pages": result.pages,
        "overfull_boxes": result.overfull,
        "warnings": result.warnings,
    }
    if template.page_budget and result.pages and result.pages > template.page_budget:
        payload["warnings"] = list(payload["warnings"]) + [
            f"Rendered {result.pages} pages; this template's budget is "
            f"{template.page_budget}. Trim content or pick another template."
        ]
    return json.dumps(payload, indent=2)


def career_templates(params: dict, ctx=None, **kw) -> str:
    """List the templates available, and the slots each one uses."""
    workspace = _resolve_workspace(ctx, params.get("workspace"),
                                   params.get("name"))
    templates = discover_templates(*_shipped_template_roots(workspace))
    out = []
    for t in templates:
        out.append({
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "dialect": t.dialect,
            "engine": t.engine,
            "columns": t.columns,
            "slots": slots_in(t),
            "source": "local" if t.dir.is_relative_to(workspace.local_templates_dir)
                      else "shipped",
        })
    return json.dumps({
        "ok": True,
        "templates": out,
        "tectonic": find_tectonic() or "not installed",
    }, indent=2)


def _recipe_skeleton(template, proposals) -> dict:
    """The recipe a set of proposals would become.

    Only the CLEAN proposals become knobs. A flagged candidate is
    reported, not written: a knob that the generator knows is a trap
    would be a knob that does nothing.
    """
    return {
        "id": template.id,
        "knobs": [p.as_knob() for p in proposals if p.is_clean],
    }


def career_configure(params: dict, ctx=None, **kw) -> str:
    """Propose a recipe for a template, and generate a configured copy.

    Two modes:

    * ``action: "propose"`` (the default) — read a template, propose
      the settings it could expose, and flag the ones that need a human
      decision. It writes nothing. The proposals are a starting point
      for a ``recipe.yaml``, which the caller then edits.

    * ``action: "generate"`` — take a base template, apply settings,
      and write a new template directory. The base must already have a
      ``recipe.yaml``; the settings are checked against it.

    The generated template lands in the workspace's ``templates.local/``,
    so nothing it contains reaches the plugin's shipped templates.
    """
    action = (params.get("action") or "propose").lower()
    template_id = params.get("template")
    new_id = params.get("id")
    template_name = params.get("template_name")
    description = params.get("description")
    settings = params.get("settings") or {}
    workspace = params.get("workspace")
    workspace_name = params.get("name")
    path = params.get("path")
    overwrite = bool(params.get("overwrite"))

    resolved = _resolve_workspace(ctx, workspace, workspace_name)
    templates = discover_templates(*_shipped_template_roots(resolved))
    if not templates:
        return json.dumps({
            "ok": False,
            "error": f"No templates found under {SHIPPED_TEMPLATES}.",
        }, indent=2)

    template = None
    for t in templates:
        if t.id == template_id:
            template = t
            break
    if template is None:
        if template_id:
            return json.dumps({
                "ok": False,
                "error": f"No template {template_id!r}. Available: "
                         f"{', '.join(t.id for t in templates)}.",
            }, indent=2)
        template = templates[0]

    if action == "propose":
        return _configure_propose(template, resolved)
    if action == "author":
        return _configure_author(template, resolved, path, overwrite)
    if action == "generate":
        return _configure_generate(template, resolved, new_id, settings,
                                   template_name, description)
    return json.dumps({
        "ok": False,
        "error": f"Unknown action {action!r}. Use 'propose', 'author' "
                 f"or 'generate'.",
    }, indent=2)


def _configure_author(template, workspace, path, overwrite: bool) -> str:
    """Propose, prove, and write the recipe.

    The order matters. The generator *proposes*; the liveness check
    *disposes*. So the recipe that gets written contains only the
    settings a render proved change the page -- a setting that does
    nothing is not offered as a setting.

    The recipe goes where the person says -- normally a template in
    the workspace's ``templates.local/``. The shipped templates are
    left alone: a concrete recipe belongs to whoever owns the
    template, not to the plugin.
    """
    if not path:
        return json.dumps({
            "ok": False,
            "error": "Give me the directory to write the recipe into "
                     "(the 'path' parameter).",
        }, indent=2)
    directory = Path(path)
    if not directory.is_dir():
        return json.dumps({
            "ok": False,
            "error": f"{directory} is not a directory.",
        }, indent=2)

    class_source = ""
    for class_file in sorted(directory.glob("*.cls")):
        class_source = class_file.read_text(encoding="utf-8", errors="replace")
        break
    source_file = directory / "cv.tex"
    if not source_file.exists():
        return json.dumps({
            "ok": False,
            "error": f"{source_file} does not exist; a recipe points at the "
                     f"lines of a template's cv.tex.",
        }, indent=2)
    source = source_file.read_text(encoding="utf-8")

    recipe = propose_recipe(
        source,
        template.id,
        dialect_source=dialects.dialect_source(template.dialect),
        class_source=class_source,
    )
    if not recipe["knobs"]:
        return json.dumps({
            "ok": False,
            "error": "Nothing safe to propose for this template.",
            "template": template.id,
        }, indent=2)

    # Prove each proposed setting with a render, and keep only the ones
    # that change the page.
    report = None
    try:
        profile = workspace.load()
    except CareerProfileError:
        profile = None
    if profile is not None:
        try:
            report = check_knobs(directory, recipe, profile)
        except Exception as exc:
            report = None
            report_error = str(exc)
        else:
            report_error = ""
    else:
        report_error = "the workspace has no career profile to render with"

    payload = {
        "ok": True,
        "template": template.id,
        "proposed": [k["id"] for k in recipe["knobs"]],
    }
    if report is not None and not report.skipped:
        recipe = keep_live(recipe, report)
        payload["live"] = report.live_ids
        payload["dead"] = report.dead_ids
        payload["dead_reasons"] = {
            k.knob_id: k.reason for k in report.knobs if not k.live
        }
        if not recipe["knobs"]:
            return json.dumps({
                "ok": False,
                "error": "None of the proposed settings changes the page, so "
                         "there is no recipe worth writing.",
                "template": template.id,
                "dead_reasons": payload["dead_reasons"],
            }, indent=2)
    else:
        payload["proven"] = False
        payload["why"] = (report.skipped if report is not None
                          else report_error)
        payload["note"] = ("The settings were not proved with a render, so "
                            "the recipe holds every proposal. Check them "
                            "before you trust them.")

    try:
        written = write_recipe(directory, recipe, overwrite=overwrite)
    except RecipeError as exc:
        return json.dumps({
            "ok": False,
            "error": str(exc),
            "template": template.id,
        }, indent=2)

    payload["written"] = str(written)
    payload["knobs"] = [k["id"] for k in recipe["knobs"]]
    payload["next"] = "Edit the recipe, then re-run the check on anything "\
                      "you add."
    return json.dumps(payload, indent=2)


def _configure_propose(template, workspace) -> str:
    """Propose the settings a template could expose. Writes nothing."""
    class_source = ""
    for class_file in sorted(template.dir.glob("*.cls")):
        class_source = class_file.read_text(encoding="utf-8", errors="replace")
        break
    proposals = propose(
        template.source,
        template.id,
        dialect_source=dialects.dialect_source(template.dialect),
        class_source=class_source,
    )
    overrides = inline_spacing_overrides(
        template.source, dialects.dialect_source(template.dialect))

    clean = [p for p in proposals if p.is_clean]
    flagged = [p for p in proposals if not p.is_clean]

    payload = {
        "ok": True,
        "template": template.id,
        "proposed_recipe": _recipe_skeleton(template, proposals),
        "proposals": [p.to_dict() for p in clean],
        "flagged": [p.to_dict() for p in flagged],
        "notes": [
            "The generator PROPOSES; a render proves. A setting is only "
            "real once changing it changes the page -- the per-knob "
            "liveness check is what settles it.",
            "The flagged candidates are traps: each one would be a "
            "control that does nothing, or a LaTeX error.",
        ],
    }
    if overrides:
        payload["dialect_overrides"] = overrides
    return json.dumps(payload, indent=2)


def _configure_generate(template, workspace, new_id, settings,
                        template_name, description) -> str:
    """Apply settings to a base template and write the result."""
    if not new_id:
        return json.dumps({
            "ok": False,
            "error": "Give the generated template an id (the 'id' parameter).",
        }, indent=2)
    if not isinstance(settings, dict):
        return json.dumps({
            "ok": False,
            "error": "The 'settings' parameter must be an object of "
                     "setting id to value.",
        }, indent=2)

    dest = workspace.local_templates_dir / new_id
    try:
        generate_template(
            template.dir,
            dest,
            settings,
            template_id=new_id,
            name=template_name or new_id,
            description=description or "",
        )
    except GenerateError as exc:
        return json.dumps({
            "ok": False,
            "error": str(exc),
            "template": template.id,
            "hint": "A template needs a recipe.yaml to be generated from. "
                    "Propose one with action='propose'.",
        }, indent=2)

    return json.dumps({
        "ok": True,
        "template": template.id,
        "generated": str(dest),
        "settings": settings,
        "next": f"Build it with career_build, using --template {new_id}.",
    }, indent=2)


TOOLS = [
    {
        "name": "career_init",
        "description": (
            "Create a named career workspace. The name becomes a directory "
            "directly under the Hermes home (one level deeper than the home "
            "itself), e.g. the name 'jane' creates <hermes home>/jane/. The "
            "workspace holds career.yaml (the structured source of truth), "
            "notes/ (prose nuance), raw/ (immutable ingested sources), targets/ "
            "(per-job match results), out/ (rendered PDFs) and templates.local/ "
            "(the user's own LaTeX templates). The name is recorded in the "
            "career profile, so it travels with the data. Returns the first "
            "intake question. Use this once at the start of a career "
            "conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short name for the workspace: lowercase "
                                   "letters, digits, dot, dash, underscore.",
                },
            },
            "required": ["name"],
        },
        "handler": career_init,
    },
    {
        "name": "career_list",
        "description": (
            "List the career workspaces that live under the Hermes home, with "
            "the name, path, and tagline of each. Use this to find the name to "
            "pass to the other career tools."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "handler": career_list,
    },
    {
        "name": "career_ingest",
        "description": (
            "Store a source document (a CV, a repo README, a project page) in the "
            "workspace's raw layer with a content hash. The raw layer is "
            "immutable: re-ingesting identical content is a no-op, and changed "
            "content is stored as a new document rather than overwriting the old."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The document text."},
                "source": {
                    "type": "string",
                    "description": "Where it came from (a URL, a file path, a label).",
                },
                "kind": {
                    "type": "string",
                    "description": "resume | repo | page | document | link",
                },
                "workspace": {"type": "string", "description": "Workspace path override."},
                "name": {"type": "string", "description": "Workspace name."},
            },
            "required": ["text"],
        },
        "handler": career_ingest,
    },
    {
        "name": "career_profile",
        "description": (
            "Read the profile, or update it. The update action takes a patch of "
            "top-level fields and merges them: a patch changes only what it "
            "names, so {\"person\": {\"tagline\": \"x\"}} leaves the name, email "
            "and links alone. Lists of records (experience, projects, education, "
            "milestones) merge by id, so one entry can be updated without "
            "resending the rest; lists of values (skills, languages, links) "
            "are replaced. Invalid patches are rejected with the specific "
            "validation errors and nothing is written."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "update"],
                    "description": "read (default) or update.",
                },
                "patch": {
                    "type": ["object", "string"],
                    "description": "Top-level profile fields to merge in.",
                },
                "workspace": {"type": "string"},
                "name": {"type": "string", "description": "Workspace name."},
            },
        },
        "handler": career_profile,
    },
    {
        "name": "career_match",
        "description": (
            "Match a job description against the profile. The match is "
            "deterministic: it extracts the job's terms, weights them, and "
            "ranks the profile's entries by how much of the job they cover. "
            "Returns the ranked entries with the terms each one matched, plus "
            "the job's terms that nothing in the profile covers. Use this to "
            "decide what to include before writing any wording."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "job_description": {"type": "string"},
                "job_description_path": {"type": "string"},
                "target": {
                    "type": "string",
                    "description": "A name for this application, e.g. 'acme-senior-engineer'.",
                },
                "workspace": {"type": "string"},
                "name": {"type": "string", "description": "Workspace name."},
            },
        },
        "handler": career_match,
    },
    {
        "name": "career_build",
        "description": (
            "Render the profile into a LaTeX CV and compile it to a PDF with "
            "tectonic. With `target`, the CV is tailored using a saved match "
            "report; without it, every visible entry is included. Returns the "
            "PDF path, the page count, and the overfull-box count."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "description": "Template id. Omit for the default.",
                },
                "target": {
                    "type": "string",
                    "description": "A saved match report to tailor against.",
                },
                "workspace": {"type": "string"},
                "name": {"type": "string", "description": "Workspace name."},
                "keep_workdir": {
                    "type": "boolean",
                    "description": "Keep the LaTeX build directory for debugging.",
                },
            },
        },
        "handler": career_build,
    },
    {
        "name": "career_templates",
        "description": (
            "List the LaTeX templates available, with the slots each one uses. "
            "Shipped templates live in the plugin; the user's own live in the "
            "workspace's templates.local/ and override a shipped template with "
            "the same id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "name": {"type": "string", "description": "Workspace name."},
            },
        },
        "handler": career_templates,
    },
    {
        "name": "career_configure",
        "description": (
            "Configure a CV template. With action='propose' (the default), "
            "read a template and propose the settings it could expose: the "
            "knobs, the line each one rewrites, and the candidates that "
            "are traps — a colour that is declared but never used, a "
            "derived colour, a class option, a line the renderer "
            "overrides. It writes nothing; the proposals are a starting "
            "point for a recipe.yaml. With action='generate', apply "
            "settings to a base template that has a recipe and write the "
            "result into the workspace's templates.local/, where the "
            "existing career_build renders it with no changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["propose", "author", "generate"],
                    "description": (
                        "propose (default) reports the settings a template "
                        "could expose; author writes that recipe into a "
                        "template directory; generate applies settings to a "
                        "base that has a recipe and writes the result."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "The template directory to write the "
                                   "recipe into (action='author').",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Replace an existing recipe "
                                   "(action='author').",
                },
                "template": {
                    "type": "string",
                    "description": "The base template id. Omit for the default.",
                },
                "id": {
                    "type": "string",
                    "description": "The id for the generated template "
                                   "(action='generate').",
                },
                "template_name": {
                    "type": "string",
                    "description": "A display name for the generated template.",
                },
                "description": {
                    "type": "string",
                    "description": "A description for the generated template.",
                },
                "settings": {
                    "type": "object",
                    "description": "Setting id to value, for action='generate'.",
                },
                "workspace": {"type": "string"},
                "name": {
                    "type": "string",
                    "description": "Workspace name.",
                },
            },
        },
        "handler": career_configure,
    },
]


# --------------------------------------------------------------------------
# Slash command
# --------------------------------------------------------------------------

def career_command(raw_args: str, ctx=None, **kw) -> str:
    """``/career`` — a thin dispatcher over the tools, for chat use."""
    args = (raw_args or "").strip()
    if not args:
        return (
            "Career commands:\n"
            "  /career init <name>   create a named workspace under the Hermes home\n"
            "  /career list          list the workspaces under the Hermes home\n"
            "  /career status        show the workspace and intake progress\n"
            "  /career templates     list the available CV templates\n"
            "  /career build [target] render a CV (add --template <id>)\n"
            "  /career match <file>  match a job description against the profile\n"
            "  /career recipe [id]   propose the settings a template could expose\n"
            "  /career author <id> --path <dir>\n"
            "                        write that recipe into a template directory\n"
            "  /career generate --base <id> --id <new-id> [--set k=v]\n"
        )

    parts = args.split()
    verb = parts[0].lower()
    rest = parts[1:]

    if verb == "init":
        return career_init({"name": rest[0]} if rest else {}, ctx=ctx)
    if verb == "list":
        return career_list({}, ctx=ctx)
    if verb == "status":
        return _status(ctx)
    if verb == "templates":
        return career_templates({}, ctx=ctx)
    if verb == "build":
        params: dict = {}
        if "--template" in rest:
            i = rest.index("--template")
            if i + 1 < len(rest):
                params["template"] = rest[i + 1]
        others = [p for p in rest if not p.startswith("--")
                  and p != params.get("template")]
        if others:
            params["target"] = others[0]
        return career_build(params, ctx=ctx)
    if verb == "match":
        if not rest:
            return "Give me a job description file: /career match job.txt"
        path = Path(rest[0]).expanduser()
        if not path.exists():
            return f"No such file: {path}"
        return career_match({
            "job_description_path": str(path),
            "target": path.stem,
        }, ctx=ctx)
    if verb == "recipe":
        return career_configure({
            "action": "propose",
            "template": rest[0] if rest else None,
        }, ctx=ctx)
    if verb == "author":
        params = {"action": "author"}
        others = []
        i = 0
        while i < len(rest):
            item = rest[i]
            if item == "--path" and i + 1 < len(rest):
                params["path"] = rest[i + 1]
                i += 2
                continue
            if item == "--template" and i + 1 < len(rest):
                params["template"] = rest[i + 1]
                i += 2
                continue
            if item == "--overwrite":
                params["overwrite"] = True
                i += 1
                continue
            others.append(item)
            i += 1
        if others and "template" not in params:
            params["template"] = others[0]
        return career_configure(params, ctx=ctx)
    if verb == "generate":
        params: dict = {"action": "generate"}
        settings: dict = {}
        others = []
        i = 0
        while i < len(rest):
            item = rest[i]
            if item == "--base" and i + 1 < len(rest):
                params["template"] = rest[i + 1]
                i += 2
                continue
            if item == "--id" and i + 1 < len(rest):
                params["id"] = rest[i + 1]
                i += 2
                continue
            if item == "--set" and i + 1 < len(rest):
                key, _, value = rest[i + 1].partition("=")
                settings[key] = value
                i += 2
                continue
            others.append(item)
            i += 1
        params["settings"] = settings
        return career_configure(params, ctx=ctx)

    return f"Unknown command {verb!r}. Try /career with no arguments for the list."


def _status(ctx=None) -> str:
    workspace = _resolve_workspace(ctx)
    if not workspace.exists():
        # Point at the workspaces that do exist, if any, so the answer is
        # a name to pass rather than a path to guess.
        available = [w["name"] for w in list_workspaces()]
        message = "No workspace yet. Create one with /career init <name>."
        if available:
            message = (f"No workspace selected. Pass a name — "
                       f"{', '.join(available)} — or create one with "
                       f"/career init <name>.")
        return json.dumps({
            "ok": False,
            "workspace": str(workspace.root),
            "available": available,
            "message": message,
        }, indent=2)

    state = ws_mod.load_checklist(workspace)
    progress = ws_mod.intake_progress(state)
    # Probe whether the profile loads. Only the raise-or-not matters here —
    # the loaded object is not needed for the status line.
    try:
        workspace.load()
        profile_status = "valid"
        profile_error = None
    except CareerProfileError as exc:
        profile_status = "invalid"
        profile_error = str(exc)

    return json.dumps({
        "ok": True,
        "workspace": str(workspace.root),
        "career_profile": str(workspace.career_path),
        "profile_status": profile_status,
        "profile_error": profile_error,
        "intake": progress,
        "next_question": (
            ws_mod.next_question(state)["prompt"] if ws_mod.next_question(state)
            else None
        ),
        "templates": [t.id for t in discover_templates(*_shipped_template_roots(workspace))],
        "tectonic": find_tectonic() or "not installed",
    }, indent=2)


# --------------------------------------------------------------------------
# CLI subcommand
# --------------------------------------------------------------------------

def career_cli(args) -> int:
    """``hermes career ...`` — the same operations from a shell.

    Hermes calls this with the parsed argparse namespace, so the
    subcommand and its options arrive as attributes.
    """
    command = getattr(args, "evergreen_command", None)

    if command == "init":
        print(career_init({"name": getattr(args, "name", None)}))
        return 0
    if command == "list":
        print(career_list({}))
        return 0
    if command == "status":
        print(_status())
        return 0
    if command == "templates":
        print(career_templates({}))
        return 0
    if command == "match":
        payload = {"job_description_path": getattr(args, "job_description", None),
                   "target": getattr(args, "target", None)}
        print(career_match(payload))
        return 0
    if command == "build":
        print(career_build({"template": getattr(args, "template", None),
                            "target": getattr(args, "target", None)}))
        return 0
    if command == "recipe":
        print(career_configure({
            "action": "propose",
            "template": getattr(args, "template", None),
            "workspace": getattr(args, "workspace", None),
        }))
        return 0
    if command == "author":
        print(career_configure({
            "action": "author",
            "template": getattr(args, "template", None),
            "path": getattr(args, "path", None),
            "overwrite": bool(getattr(args, "overwrite", False)),
            "workspace": getattr(args, "workspace", None),
        }))
        return 0
    if command == "generate":
        settings = {}
        for item in getattr(args, "sets", None) or []:
            key, _, value = item.partition("=")
            if not _:
                print(f"--set needs KEY=VALUE, got {item!r}")
                return 1
            settings[key] = value
        print(career_configure({
            "action": "generate",
            "template": getattr(args, "base", None),
            "id": getattr(args, "new_id", None),
            "settings": settings,
            "workspace": getattr(args, "workspace", None),
        }))
        return 0

    print("Usage: hermes career "
          "{init <name> | list | status | templates | match <file> | build "
          "| recipe [template] | author <template> --path <dir> | generate "
          "--base <id> --id <new-id>}")
    return 1


def setup_cli(parser) -> None:
    """Wire the ``career`` subcommand into the argparse tree.

    Hermes hands ``setup_fn`` the ArgumentParser it created for this
    command, and calls the handler with the parsed namespace.
    """
    parser.set_defaults(func=career_cli)
    sub = parser.add_subparsers(dest="evergreen_command")

    p = sub.add_parser("init", help="Create a named workspace under the Hermes home.")
    p.add_argument("name")

    sub.add_parser("list", help="List the workspaces under the Hermes home.")
    sub.add_parser("status", help="Show the workspace and intake progress.")
    sub.add_parser("templates", help="List the available CV templates.")

    p = sub.add_parser("match", help="Match a job description against the profile.")
    p.add_argument("job_description")
    p.add_argument("--target", default="untitled")

    p = sub.add_parser("build", help="Render and compile a CV.")
    p.add_argument("--template", default=None)
    p.add_argument("--target", default=None)

    p = sub.add_parser(
        "recipe",
        help="Propose the settings a template could expose.",
    )
    p.add_argument("template", nargs="?", default=None)
    p.add_argument("--workspace", default=None)

    p = sub.add_parser(
        "author",
        help="Write the proposed recipe into a template directory.",
    )
    p.add_argument("template", nargs="?", default=None)
    p.add_argument("--path", dest="path", default=None,
                   help="The template directory to write recipe.yaml into.")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--workspace", default=None)

    p = sub.add_parser(
        "generate",
        help="Generate a configured template from a base that has a recipe.",
    )
    p.add_argument("--base", dest="base", default=None)
    p.add_argument("--id", dest="new_id", default=None)
    p.add_argument("--set", dest="sets", action="append", default=[],
                   metavar="KEY=VALUE")
    p.add_argument("--workspace", default=None)


# --------------------------------------------------------------------------
# Plugin registration
# --------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the plugin's tools, skills, command, and CLI subcommand."""
    for tool in TOOLS:
        ctx.register_tool(
            name=tool["name"],
            toolset="career",
            schema={
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
            handler=tool["handler"],
        )

    ctx.register_command(
        name="career",
        handler=career_command,
        description="Manage the career workspace: init, status, templates, "
                    "match, build.",
        args_hint="[init <path> | status | templates | match <file> | build [target]]",
    )

    ctx.register_cli_command(
        name="career",
        help="Manage the Evergreen career workspace and build CVs.",
        setup_fn=setup_cli,
        handler_fn=career_cli,
    )

    skills_dir = PLUGIN_ROOT / "skills"
    if skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir()):
            if not (skill_dir / "SKILL.md").exists():
                continue
            try:
                ctx.register_skill(name=skill_dir.name, path=skill_dir / "SKILL.md")
            except (ValueError, FileNotFoundError):
                continue


__all__ = ["register", "career_command", "career_cli", "setup_cli", "TOOLS"]
