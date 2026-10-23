"""Evergreen's dashboard backend: the Configurator's HTTP door.

Mounted at ``/api/plugins/evergreen/`` by Hermes' dashboard router, this file
makes the whole feature reachable over HTTP with no UI at all:

    GET  /templates    every template, its recipe, and its knobs
    GET  /workspaces   the career workspaces this machine can name
    POST /generate     settings -> a new directory in ``templates.local/``
    POST /build        a template -> a real tectonic build -> PDF, page count

**Why every handler is thin.** Each one calls the plugin's own Python — the
same ``career_build``, ``generate_template``, ``load_recipe`` and
``discover_templates`` that ``hermes career ...`` and the agent's tools call.
A door that re-implemented any of it would be a second place for the recipe
rules (exactly-one-match, defaults-are-a-no-op) to drift, and the drift would
be invisible until a template rendered wrong.

Two things about how Hermes loads this file (recorded in §1 of
``CONFIGURATOR-PLAN.md``, and both are load-bearing):

* It is imported as a **standalone module** — ``spec_from_file_location``
  under the name ``hermes_dashboard_plugin_evergreen`` — not as part of the
  agent half's package. Relative imports into ``evergreen/`` therefore do not
  resolve; the plugin root goes on ``sys.path`` and the package is imported
  from there. That is a *second* copy of the module, distinct from the agent
  half's. It is fine here because everything reached through it is a pure
  function over files, and it is noted because it is invisible until it bites.
* It is imported only when the plugin is in ``plugins.enabled`` in
  ``config.yaml``. Evergreen is.

**Why the handlers are plain ``def``.** FastAPI runs a sync handler in its
threadpool, so a tectonic build (seconds to a minute) cannot block the event
loop the rest of the dashboard is served from. An ``async def`` that called
``build_tex`` would.

**What this door deliberately does not do.** It does not guess a workspace
from the process's working directory: a server's cwd is not the user's, and
``find_workspace`` walking up from it would act on whatever directory the
gateway happened to start in. A request names its workspace (see
``/workspaces``), or the operator's configured one is used, or there is exactly
one and it is used.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# The plugin root, not this file's directory: ``evergreen/`` is its sibling,
# and Hermes loaded this file without putting the root on sys.path.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from fastapi import APIRouter, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from evergreen import (  # noqa: E402
    SHIPPED_TEMPLATES,
    career_build,
    discover_templates,
    slots_in,
)
from evergreen.build import (  # noqa: E402
    BuildError,
    find_tectonic,
    tectonic_install_hint,
)
from evergreen.career import (  # noqa: E402
    CareerProfileError,
    Workspace,
    hermes_home,
    list_workspaces,
    workspace_path_for_name,
)
from evergreen.generate import (  # noqa: E402
    SETTINGS_FILENAME,
    GenerateError,
    generate_template,
)
from evergreen.recipe import (  # noqa: E402
    RECIPE_FILENAME,
    RecipeError,
    has_recipe,
    load_recipe,
)

router = APIRouter()

PLUGIN_ID = "evergreen"


# --------------------------------------------------------------------------
# Workspaces
# --------------------------------------------------------------------------

def _configured_workspace_path() -> str | None:
    """The plugin's own ``workspace_path`` setting, when the operator set one.

    The agent half reads this through ``ctx.get_config``; this module is not
    loaded by the plugin loader and has no ``ctx``, so it reads the same
    ``plugins.entries.<id>.settings.workspace_path`` key from the live config.
    A door that ignored the setting would act on a different workspace than the
    tools do — the kind of difference nobody notices until a CV lands in the
    wrong directory.
    """
    try:
        from hermes_cli.config import load_config

        config = load_config() or {}
    except Exception:
        return None
    entries = (config.get("plugins") or {}).get("entries") or {}
    settings = (entries.get(PLUGIN_ID) or {}).get("settings") or {}
    value = settings.get("workspace_path")
    return str(value) if value else None


def _candidates() -> list[dict[str, Any]]:
    """Every workspace this machine can address without guessing.

    Two sources, both explicit: the named workspaces sitting directly under the
    Hermes home, and the one the operator configured. A workspace anywhere else
    is addressed by path — there is no registry to consult, by design (the name
    lives in the profile, so the data stays portable).
    """
    found: list[dict[str, Any]] = []
    for entry in list_workspaces():
        found.append({**entry, "source": "named"})

    configured = _configured_workspace_path()
    if configured:
        workspace = Workspace(Path(configured).expanduser())
        named = {str(Path(e["path"]).resolve(strict=False)) for e in found}
        key = str(workspace.root.resolve(strict=False))
        if workspace.exists() and key not in named:
            found.append({
                "name": workspace.root.name,
                "path": str(workspace.root),
                "tagline": "",
                "profile_status": "valid",
                "source": "configured",
            })
    return found


def _looks_like_path(value: str) -> bool:
    """Whether a ``workspace`` value is a filesystem path rather than a name.

    Absolute paths, a leading ``~``, or anything carrying a separator. A bare
    word is a name — a workspace is created under the Hermes home and referred
    to by that name, which is the handle the whole plugin uses.
    """
    return (
        value.startswith(("~", ".", "/", "\\"))
        or "/" in value
        or "\\" in value
        or (len(value) > 1 and value[1] == ":")
    )


def _workspace(param: str | None) -> Workspace:
    """Resolve the workspace a request acts on, or say why it cannot.

    A ``workspace`` value is a name (``<hermes home>/<name>/``) or a path to any
    directory holding a ``career.yaml``. Both are checked in that order, because
    a name is the primary handle and a path is the escape hatch.

    A name is resolved against what ``/workspaces`` lists before it is resolved
    as a Hermes-home directory name, so the two never disagree about a
    workspace both of them know (see the note below).
    """
    text = (param or "").strip()
    if text:
        # A value is a PATH only when it looks like one; otherwise it is a
        # workspace name. Resolving a bare name against the process's working
        # directory would let the server's cwd silently choose the workspace,
        # which is exactly the guess this door refuses to make.
        if _looks_like_path(text):
            candidate = Workspace(Path(text).expanduser())
            if candidate.exists():
                return candidate
        else:
            # A listed workspace resolves by the name the LISTING used. For a
            # workspace named under the Hermes home that is the same thing as
            # `workspace_path_for_name`; for the configured one it is not, since
            # the listing names it after its directory, which can live anywhere.
            # Without this, the pane's picker handed back a name this door then
            # refused, and the refusal named the workspace it had just rejected.
            for entry in _candidates():
                if entry["name"] == text:
                    listed = Workspace(Path(entry["path"]))
                    if listed.exists():
                        return listed
            try:
                named = Workspace(workspace_path_for_name(text))
            except CareerProfileError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if named.exists():
                return named
        known = ", ".join(entry["name"] for entry in _candidates()) or "none"
        raise HTTPException(
            status_code=404,
            detail=(f"No career workspace at {text!r}: neither a path holding "
                    f"career.yaml nor a workspace name. Known workspaces: {known}."),
        )

    candidates = _candidates()
    # Nothing was named. The operator's configured workspace IS the answer: it is
    # a setting, not a guess, and preferring it keeps a first-ever pane load
    # (which sends no workspace at all) from failing on a machine that also has
    # named workspaces under its Hermes home.
    configured = _configured_workspace_path()
    if configured:
        workspace = Workspace(Path(configured).expanduser())
        if workspace.exists():
            return workspace
    if len(candidates) == 1:
        return Workspace(Path(candidates[0]["path"]))
    if not candidates:
        raise HTTPException(
            status_code=400,
            detail=("No career workspace on this machine. Create one with "
                    "`hermes career init <name>`, or pass the path of an "
                    "existing workspace as `workspace`."),
        )
    names = ", ".join(entry["name"] for entry in candidates)
    raise HTTPException(
        status_code=400,
        detail=f"This machine has several workspaces ({names}); pass `workspace`.",
    )


def _optional_workspace(param: str | None) -> tuple[Workspace | None, str | None]:
    """The workspace, or None plus the reason it could not be found.

    ``GET /templates`` still has something true to say without a workspace —
    the shipped templates — so a missing one is reported, not raised.
    """
    try:
        return _workspace(param), None
    except HTTPException as exc:
        return None, str(exc.detail)


def _describe(workspace: Workspace) -> dict[str, Any]:
    return {"name": workspace.root.name, "path": str(workspace.root)}


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

def _roots(workspace: Workspace | None) -> list[Path]:
    """Template roots in override order: shipped first, the workspace's last."""
    roots = [SHIPPED_TEMPLATES]
    if workspace is not None:
        roots.append(workspace.local_templates_dir)
    return roots


def _template_rows(workspace: Workspace | None) -> list[dict[str, Any]]:
    """Every template, with the recipe that makes it a base for generation.

    A template is generatable only when its recipe loads *and* agrees with the
    template's own id. A recipe that is present but broken is reported in
    ``recipe_error`` rather than dropped: a form that silently hides a template
    is harder to fix than one that says what is wrong with it.
    """
    local_root = None
    if workspace is not None:
        local_root = workspace.local_templates_dir.resolve(strict=False)

    rows: list[dict[str, Any]] = []
    for template in discover_templates(*_roots(workspace)):
        directory = template.dir.resolve(strict=False)
        source = "local" if local_root is not None and directory.is_relative_to(local_root) else "shipped"
        row: dict[str, Any] = {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "dialect": template.dialect,
            "engine": template.engine,
            "columns": template.columns,
            "slots": slots_in(template),
            "source": source,
            "path": str(template.dir),
            "has_recipe": has_recipe(template.dir),
            "recipe": None,
            "recipe_error": None,
            "generate_from": False,
        }
        if row["has_recipe"]:
            try:
                recipe = load_recipe(template.dir)
            except RecipeError as exc:
                row["recipe_error"] = str(exc)
            else:
                if recipe.id != template.id:
                    row["recipe_error"] = (
                        f"the recipe says id {recipe.id!r}, the template says "
                        f"{template.id!r}; they describe the same template."
                    )
                else:
                    row["recipe"] = recipe.as_dict()
                    row["generate_from"] = True
        rows.append(row)
    return rows


def _resolved(recipe, settings: dict[str, Any]) -> list[dict[str, Any]]:
    """The LaTeX each knob now stands for, defaults included.

    This is the same resolution ``apply_settings`` performs when it rewrites
    ``cv.tex`` — asked for separately so the caller can see what was written
    without reading the file back. ``changed`` says which of them the caller
    actually chose, which is what a preview needs.
    """
    out: list[dict[str, Any]] = []
    for knob in recipe.knobs:
        value = settings.get(knob.id, knob.default)
        out.append({
            "knob": knob.id,
            "label": knob.label,
            "value": knob.normalize(value),
            "latex": knob.render(value),
            "changed": str(value) != str(knob.default),
        })
    return out


def _check_settings(recipe, settings: dict[str, Any]) -> None:
    """Reject a setting the template does not offer, before anything is written."""
    unknown = sorted(set(settings) - {knob.id for knob in recipe.knobs})
    if unknown:
        known = ", ".join(knob.id for knob in recipe.knobs) or "(none)"
        raise HTTPException(
            status_code=400,
            detail=(f"Unknown setting(s): {', '.join(unknown)}. This template "
                    f"offers: {known}."),
        )


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@router.get("/templates")
def get_templates(workspace: str | None = None) -> dict[str, Any]:
    """Every template, its recipe, and its knobs.

    With no ``workspace`` and none resolvable, this still lists the shipped
    templates — which carry no recipe, because the plugin ships the mechanism
    and whoever owns a template ships its choices — and says in
    ``workspace_error`` why no workspace was found. A workspace that *was* named
    and cannot be found is a 404, like every other route's.
    """
    if (workspace or "").strip():
        ws, workspace_error = _workspace(workspace), None
    else:
        ws, workspace_error = _optional_workspace(None)
    rows = _template_rows(ws)
    return {
        "ok": True,
        "hermes_home": str(hermes_home()),
        "workspace": _describe(ws) if ws is not None else None,
        "workspace_error": workspace_error,
        "templates": rows,
        "bases": [row["id"] for row in rows if row["generate_from"]],
        "tectonic": find_tectonic(),
    }


@router.get("/workspaces")
def get_workspaces() -> dict[str, Any]:
    """The career workspaces this machine can name, and how to address one."""
    entries = []
    for entry in _candidates():
        workspace = Workspace(Path(entry["path"]))
        local = workspace.local_templates_dir
        templates = [t.id for t in discover_templates(local)] if local.is_dir() else []
        entries.append({**entry, "templates_local": templates})

    return {
        "ok": True,
        "hermes_home": str(hermes_home()),
        "workspaces": entries,
        "count": len(entries),
        "default": entries[0]["name"] if len(entries) == 1 else None,
        "tectonic": find_tectonic(),
        "hint": ("Pass `workspace` as a name, or as the path of any directory "
                 "holding a career.yaml."),
    }


class GenerateRequest(BaseModel):
    """A configuration to generate from."""

    base: str = Field(..., description="Id of the base template; it must carry a recipe.")
    id: str = Field(..., description="Id, and directory name, of the new template.")
    name: str = Field("", description="Shown as the template's name. Defaults to the id.")
    description: str = Field("", description="Shown as the template's description.")
    workspace: str | None = Field(None, description="Workspace name or path.")
    settings: dict[str, Any] = Field(
        default_factory=dict,
        description="Setting id -> value. Omitted settings keep the base's value.",
    )


@router.post("/generate")
def post_generate(request: GenerateRequest) -> dict[str, Any]:
    """Apply settings to a base template and write a new template directory."""
    workspace = _workspace(request.workspace)
    templates = {t.id: t for t in discover_templates(*_roots(workspace))}

    base = templates.get(request.base)
    if base is None:
        known = ", ".join(sorted(templates)) or "(none)"
        raise HTTPException(
            status_code=404,
            detail=f"No template {request.base!r}. Available: {known}.",
        )
    if not has_recipe(base.dir):
        raise HTTPException(
            status_code=400,
            detail=(f"{base.id!r} carries no recipe.yaml, so it exposes no "
                    f"settings to configure. Author one first: "
                    f"`hermes career author {base.id} --path <template dir>`."),
        )
    try:
        recipe = load_recipe(base.dir)
    except RecipeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{base.id!r}'s recipe cannot be used: {exc}",
        ) from exc
    if recipe.id != base.id:
        raise HTTPException(
            status_code=400,
            detail=(f"the recipe in {base.id!r} says id {recipe.id!r}; a recipe "
                    f"must describe the template it sits in."),
        )

    _check_settings(recipe, request.settings)
    try:
        resolved = _resolved(recipe, request.settings)
    except RecipeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    dest = workspace.local_templates_dir / request.id
    if dest.exists() and any(dest.iterdir()):
        raise HTTPException(
            status_code=409,
            detail=(f"{dest} already exists and is not empty. Pick another id, "
                    f"or remove that directory."),
        )

    try:
        generate_template(
            base.dir,
            dest,
            request.settings,
            template_id=request.id,
            name=request.name,
            description=request.description,
        )
    except (GenerateError, RecipeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Read the result back off disk: the response reports what exists, not what
    # was meant. A generator that half-wrote would otherwise look successful.
    main_path = dest / base.main
    if not main_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"generation reported success but {main_path} is missing.",
        )

    return {
        "ok": True,
        "base": base.id,
        "id": request.id,
        "generated": str(dest),
        "paths": {
            "dir": str(dest),
            "main": str(main_path),
            "manifest": str(dest / "template.yaml"),
            "recipe": str(dest / RECIPE_FILENAME),
            "settings": str(dest / SETTINGS_FILENAME),
        },
        "settings": {
            key: recipe.knob(key).normalize(value)
            for key, value in request.settings.items()
        },
        "resolved": resolved,
        "next": f"Build it with POST /build, template={request.id!r}.",
    }


class BuildRequest(BaseModel):
    """A template to compile against the workspace's own profile."""

    template: str = Field(..., description="Template id, generated or shipped.")
    workspace: str | None = Field(None, description="Workspace name or path.")
    target: str | None = Field(None, description="A saved match report to tailor to.")
    keep_workdir: bool = Field(False, description="Keep the LaTeX build directory.")


@router.post("/build")
def post_build(request: BuildRequest) -> dict[str, Any]:
    """Render and compile a template, and report the page and overfull counts."""
    if not find_tectonic():
        raise HTTPException(status_code=503, detail=tectonic_install_hint())

    workspace = _workspace(request.workspace)
    try:
        rendered = career_build({
            "template": request.template,
            "workspace": str(workspace.root),
            "target": request.target or None,
            "keep_workdir": request.keep_workdir,
        })
    except BuildError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        payload = json.loads(rendered)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"the build returned something that is not a report: {exc}",
        ) from exc

    if payload.get("error"):
        message = str(payload["error"])
        # The renderer's own refusals: a template that does not exist, or a
        # target with no match report, are the caller's to fix.
        status = 404 if "No template" in message else 400
        raise HTTPException(status_code=status, detail=message)

    if not payload.get("ok"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "the template did not compile",
                "template": request.template,
                "errors": payload.get("errors") or [],
                "log": payload.get("log"),
            },
        )

    return payload


__all__ = ["router"]
