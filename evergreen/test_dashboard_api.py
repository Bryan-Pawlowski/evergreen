"""The backend door: the Configurator's four routes, exercised for real.

M3 of ``CONFIGURATOR-PLAN.md`` is "the whole feature is reachable over HTTP,
with no UI". This test is what makes that claim checkable without a gateway
running:

* the router is loaded **exactly the way Hermes loads it** — a standalone
  module via ``spec_from_file_location``, with no package context — so the
  ``sys.path`` bootstrap in ``plugin_api.py`` is exercised rather than
  bypassed. A separate interpreter, started with no ``PYTHONPATH`` and from an
  unrelated working directory, proves the file can find ``evergreen/`` on its
  own.
* it is mounted at the prefix taken from ``dashboard/manifest.json``, so a
  manifest ``name`` that drifts from the mount prefix fails here instead of
  404ing every call in the app.
* every route is called over HTTP against a throwaway Hermes home and a real
  workspace, and each answer is checked against **the filesystem** — not
  against the handler's own return value. A door that reports what it meant to
  do, rather than what it did, passes no test in this file.

What this test deliberately does not cover: the mount gates (``plugins.enabled``
and the request's profile scope). Those are Hermes' rules, not this plugin's,
and a re-implementation of them here would pass while the real gate did
something else. They are verified against a live ``hermes serve`` instead —
see the M3 section of the plan.

Run:  python -m evergreen.test_dashboard_api
"""

from __future__ import annotations

import atexit
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .career import CareerProfile, Workspace
from .test_recipe import SAMPLE, _Checker, _derived_recipe

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_TEMPLATES = REPO_ROOT / "templates"
DASHBOARD = REPO_ROOT / "dashboard"
PLUGIN_API = DASHBOARD / "plugin_api.py"
MANIFEST = DASHBOARD / "manifest.json"

# The two bases this test configures. ``plain`` has no class and no fonts, so a
# generation from it isolates the route from every other moving part; ``yaac``
# carries a vendored class, fonts and a fonts.conf, which is what proves the
# door copies a base's build inputs rather than only its LaTeX.
PLAIN_ID = "plain"
YAAC_ID = "yaac"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _hermes_home() -> Path:
    """A throwaway Hermes home holding one named workspace and two local bases.

    The bases are copies of shipped templates with a recipe derived at run time
    by the same proposal engine the Configurator uses — the repo ships no
    concrete recipe (the plugin ships the mechanism; whoever owns a template
    ships its choices).
    """
    home = Path(tempfile.mkdtemp(prefix="evergreen-api-home-"))
    atexit.register(shutil.rmtree, str(home), True)

    workspace = Workspace(home / "testws")
    workspace.create()
    workspace.save(CareerProfile(SAMPLE))
    for base_id in (PLAIN_ID, YAAC_ID):
        _derived_recipe(SHIPPED_TEMPLATES / base_id,
                        workspace.local_templates_dir / base_id)
    return home


def _load_router():
    """Import ``dashboard/plugin_api.py`` the way Hermes does.

    ``spec_from_file_location`` under the module name Hermes uses, registered in
    ``sys.modules`` *before* execution (FastAPI and pydantic resolve string
    annotations by module name) — the same sequence as
    ``web_server_dashboard._mount_one_plugin_api``. Nothing here puts the plugin
    root on ``sys.path`` first: if the file did not do it, the import would fail
    exactly as it would in a live server.
    """
    module_name = "hermes_dashboard_plugin_evergreen"
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN_API)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {PLUGIN_API} as a module.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.router


class _Door:
    """A TestClient whose paths are relative to the mount prefix Hermes uses.

    ``/workspaces`` here means ``/api/plugins/evergreen/workspaces``, which is
    the address the desktop pane calls with ``ctx.rest('/workspaces')``.
    """

    def __init__(self, client, prefix: str):
        self.client = client
        self.prefix = prefix.rstrip("/")

    def get(self, path: str, **kwargs):
        return self.client.get(self.prefix + path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.client.post(self.prefix + path, **kwargs)


def _client(router, prefix: str) -> _Door:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router, prefix=prefix)
    return _Door(TestClient(app), prefix)


def _clean_env() -> dict:
    """The current environment with the import paths that would hide a defect.

    ``PYTHONPATH`` is dropped so the subprocess can only reach ``evergreen/``
    through ``plugin_api.py``'s own ``sys.path`` insertion.
    """
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    return env


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_the_module_loads_like_hermes_loads_it(c: _Checker) -> None:
    """The manifest, the mount prefix and the module agree."""
    print("\n-- loading --")
    manifest = _manifest()

    c.check("manifest: it names the plugin (the mount prefix and the id must match)",
            manifest.get("name") == "evergreen",
            f"name={manifest.get('name')!r}")
    c.check("manifest: it declares the backend",
            manifest.get("api") == "plugin_api.py",
            f"api={manifest.get('api')!r}")
    c.check("manifest: the declared api file exists", PLUGIN_API.is_file(),
            str(PLUGIN_API))

    # The web dashboard loads `entry` as a classic script on every visit; a
    # manifest that names a missing file logs a load failure each time.
    entry = DASHBOARD / str(manifest.get("entry") or "dist/index.js")
    c.check("manifest: the SPA entry file exists", entry.is_file(), str(entry))
    c.check("manifest: it adds no browser tab (the Configurator is a pane)",
            bool((manifest.get("tab") or {}).get("hidden")),
            f"tab={manifest.get('tab')!r}")

    router = _load_router()
    paths = sorted(getattr(route, "path", "") for route in router.routes)
    c.check("module: it exposes a router with the four routes",
            paths == ["/build", "/generate", "/templates", "/workspaces"], str(paths))

    # The genuine bootstrap check: a fresh interpreter, an unrelated cwd, and no
    # PYTHONPATH. The only way `from evergreen import ...` resolves is the
    # sys.path insertion inside the file itself.
    scratch = tempfile.mkdtemp(prefix="evergreen-api-probe-")
    atexit.register(shutil.rmtree, scratch, True)
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('probe', {str(PLUGIN_API)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['probe'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "print(len(getattr(mod, 'router').routes))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=scratch,
                          env=_clean_env(), capture_output=True, text=True)
    c.check("module: it imports with nothing but its own sys.path bootstrap",
            proc.returncode == 0,
            (proc.stderr or proc.stdout).strip()[-400:])
    c.check("module: the standalone load finds all four routes",
            proc.stdout.strip().endswith("4"), proc.stdout.strip()[-200:])


def test_workspaces(c: _Checker, client) -> None:
    """GET /workspaces finds the workspace on this machine."""
    print("\n-- GET /workspaces --")
    response = client.get("/workspaces")
    c.check("workspaces: 200", response.status_code == 200, response.text[:300])
    payload = response.json()

    c.check("workspaces: it is ok", payload.get("ok") is True)
    names = [entry["name"] for entry in payload.get("workspaces") or []]
    c.check("workspaces: the workspace is listed", names == ["testws"], str(names))
    c.check("workspaces: one workspace is the default",
            payload.get("default") == "testws", str(payload.get("default")))

    entry = (payload.get("workspaces") or [{}])[0]
    c.check("workspaces: it reports the workspace's own templates",
            sorted(entry.get("templates_local") or []) == [PLAIN_ID, YAAC_ID],
            str(entry.get("templates_local")))
    c.check("workspaces: it reports where tectonic is", bool(payload.get("tectonic")),
            str(payload.get("tectonic")))


def test_templates(c: _Checker, client) -> None:
    """GET /templates reports every template, its recipe, and its knobs."""
    print("\n-- GET /templates --")
    response = client.get("/templates")
    c.check("templates: 200", response.status_code == 200, response.text[:300])
    payload = response.json()
    rows = {row["id"]: row for row in payload.get("templates") or []}

    c.check("templates: the workspace resolved",
            (payload.get("workspace") or {}).get("name") == "testws",
            str(payload.get("workspace")) + str(payload.get("workspace_error")))
    c.check("templates: all four shipped bases plus the local copies are listed",
            set(rows) == {"altacv", "minimal", PLAIN_ID, YAAC_ID}, str(sorted(rows)))

    plain = rows.get(PLAIN_ID) or {}
    c.check("templates[plain]: it is the workspace's copy",
            plain.get("source") == "local", str(plain.get("source")))
    c.check("templates[plain]: it carries a recipe", plain.get("has_recipe") is True)
    c.check("templates[plain]: the recipe loads", plain.get("recipe_error") is None,
            str(plain.get("recipe_error")))
    knob_ids = [k["id"] for k in (plain.get("recipe") or {}).get("knobs") or []]
    c.check("templates[plain]: its knobs are reported",
            bool(knob_ids) and {"base_size", "margin"} <= set(knob_ids),
            str(knob_ids))
    c.check("templates[plain]: it is a base to generate from",
            plain.get("generate_from") is True)
    c.check("templates[plain]: it reports the slots it uses",
            "experience" in (plain.get("slots") or []), str(plain.get("slots")))

    minimal = rows.get("minimal") or {}
    c.check("templates[minimal]: a shipped template carries no recipe",
            minimal.get("has_recipe") is False)
    c.check("templates[minimal]: so it is not offered as a base",
            minimal.get("generate_from") is False)
    c.check("templates: the bases are exactly the templates with a recipe",
            payload.get("bases") == [PLAIN_ID, YAAC_ID], str(payload.get("bases")))

    # A path is accepted as well as a name, which is how a workspace living
    # outside the Hermes home is addressed.
    path = (payload.get("workspace") or {}).get("path")
    by_path = client.get("/templates", params={"workspace": path})
    c.check("templates: a workspace path resolves too",
            by_path.status_code == 200
            and (by_path.json().get("workspace") or {}).get("name") == "testws",
            by_path.text[:300])

    unknown = client.get("/templates", params={"workspace": "nosuchws"})
    c.check("templates: an unknown workspace is a 404, not an empty list",
            unknown.status_code == 404, unknown.text[:300])
    listed = client.get("/templates", params={"workspace": "nosuchws"}).json()
    c.check("templates: and the 404 says what is known",
            "testws" in str(listed.get("detail")), str(listed.get("detail")))


def test_generate(c: _Checker, client, home: Path) -> None:
    """POST /generate writes a template directory that is on disk afterwards."""
    print("\n-- POST /generate --")
    workspace_dir = home / "testws"
    settings = {"base_size": "12pt", "margin": "1in"}

    response = client.post("/generate", json={
        "base": PLAIN_ID,
        "id": "wide",
        "name": "Wide Plain",
        "description": "A wider plain template.",
        "workspace": "testws",
        "settings": settings,
    })
    c.check("generate: 200", response.status_code == 200, response.text[:400])
    payload = response.json()

    dest = workspace_dir / "templates.local" / "wide"
    c.check("generate: the directory is on disk", dest.is_dir(), str(dest))
    c.check("generate: the response points at the file that exists",
            Path(payload.get("paths", {}).get("main", "")) == dest / "cv.tex",
            str(payload.get("paths")))

    source = (dest / "cv.tex").read_text(encoding="utf-8")
    c.check("generate: the chosen size reached cv.tex",
            "\\documentclass[12pt,letterpaper]{article}" in source)
    c.check("generate: the base's value is gone",
            "\\documentclass[10pt,letterpaper]{article}" not in source)
    c.check("generate: the chosen margin reached cv.tex",
            "\\usepackage[margin=1in]{geometry}" in source)

    manifest = (dest / "template.yaml").read_text(encoding="utf-8")
    c.check("generate: the manifest carries the new id and name",
            "id: wide" in manifest and "Wide Plain" in manifest, manifest)
    c.check("generate: the manifest keeps the base's dialect",
            "dialect: plain" in manifest, manifest)

    provenance = (dest / "settings.yaml").read_text(encoding="utf-8")
    c.check("generate: the provenance names the base", "base: plain" in provenance,
            provenance)
    c.check("generate: the provenance records the values",
            "12pt" in provenance and "1in" in provenance, provenance)

    resolved = {item["knob"]: item for item in payload.get("resolved") or []}
    rows = {row["id"]: row for row in client.get("/templates").json().get("templates") or []}
    knob_ids = [k["id"] for k in (rows.get(PLAIN_ID) or {}).get("recipe", {}).get("knobs") or []]
    c.check("generate: every knob is resolved, not only the chosen ones",
            set(resolved) == set(knob_ids), f"{sorted(resolved)} vs {knob_ids}")
    c.check("generate: the resolution marks what changed",
            resolved.get("margin", {}).get("changed") is True
            and resolved["margin"]["latex"] == "\\usepackage[margin=1in]{geometry}",
            str(resolved.get("margin")))

    # The generated template is an ordinary template: it shows up for rendering.
    c.check("generate: the result is discoverable as a local template",
            (rows.get("wide") or {}).get("source") == "local", str(rows.get("wide")))

    # Copying the base's build inputs is the other half of a self-contained
    # template directory; ``plain`` has none, so this is the case with some.
    yaac = client.post("/generate", json={
        "base": YAAC_ID, "id": "yaac-wide", "workspace": "testws", "settings": {},
    })
    c.check("generate: a base with a class and fonts generates too",
            yaac.status_code == 200, yaac.text[:400])
    yaac_dir = workspace_dir / "templates.local" / "yaac-wide"
    c.check("generate: the base's class file is copied",
            (yaac_dir / "yaac-another-awesome-cv.cls").is_file())
    c.check("generate: the base's fonts and fontconfig are copied",
            (yaac_dir / "fonts").is_dir() and (yaac_dir / "fonts.conf").is_file())


def test_generate_refuses(c: _Checker, client, home: Path) -> None:
    """The door refuses what it cannot do honestly, and writes nothing."""
    print("\n-- POST /generate, refusals --")
    workspace_dir = home / "testws"
    local = workspace_dir / "templates.local"

    again = client.post("/generate", json={
        "base": PLAIN_ID, "id": "wide", "workspace": "testws", "settings": {},
    })
    c.check("generate: an existing template is a 409, not an overwrite",
            again.status_code == 409, again.text[:300])

    unknown_setting = client.post("/generate", json={
        "base": PLAIN_ID, "id": "banana-cv", "workspace": "testws",
        "settings": {"banana": "1"},
    })
    c.check("generate: an unknown setting is a 400",
            unknown_setting.status_code == 400, unknown_setting.text[:300])
    c.check("generate: and nothing was created for it",
            not (local / "banana-cv").exists())

    bad_value = client.post("/generate", json={
        "base": PLAIN_ID, "id": "bad-size", "workspace": "testws",
        "settings": {"base_size": "12pt2"},
    })
    c.check("generate: a value outside the options is a 400",
            bad_value.status_code == 400, bad_value.text[:300])
    c.check("generate: and nothing was created for it",
            not (local / "bad-size").exists())

    # A template id becomes a directory name. An id that climbs out of
    # templates.local/ must be refused by the generator itself, so no caller
    # (CLI, tool, HTTP) can write outside the workspace.
    for bad_id in ("../escape", "sub/dir", "BadCv", ".hidden"):
        escape = client.post("/generate", json={
            "base": PLAIN_ID, "id": bad_id, "workspace": "testws", "settings": {},
        })
        c.check(f"generate: the id {bad_id!r} is refused",
                escape.status_code == 400, escape.text[:200])
    c.check("generate: nothing was written above templates.local/",
            not (workspace_dir / "escape").exists()
            and not (home / "escape").exists()
            and not (local / ".hidden").exists())

    no_recipe = client.post("/generate", json={
        "base": "minimal", "id": "from-minimal", "workspace": "testws", "settings": {},
    })
    c.check("generate: a base without a recipe is a 400",
            no_recipe.status_code == 400, no_recipe.text[:300])
    c.check("generate: and the refusal says how to author one",
            "career author" in no_recipe.text, no_recipe.text[:400])

    unknown_base = client.post("/generate", json={
        "base": "nosuchtemplate", "id": "x", "workspace": "testws", "settings": {},
    })
    c.check("generate: an unknown base is a 404",
            unknown_base.status_code == 404, unknown_base.text[:300])


def test_build(c: _Checker, client, home: Path) -> None:
    """POST /build compiles a generated template with the real engine."""
    print("\n-- POST /build --")
    response = client.post("/build", json={"template": "wide", "workspace": "testws"})
    if response.status_code == 503:
        print("[SKIP] build -- tectonic is not installed")
        return

    c.check("build: 200", response.status_code == 200, response.text[:600])
    payload = response.json()

    pdf = Path(payload.get("pdf") or "")
    c.check("build: it reports a PDF that exists", pdf.is_file(), str(pdf))
    c.check("build: the PDF is the generated template's",
            "wide" in pdf.name, pdf.name)
    c.check("build: it reports the page count",
            isinstance(payload.get("pages"), int) and payload["pages"] >= 1,
            str(payload.get("pages")))
    c.check("build: it reports the overfull-box count",
            payload.get("overfull_boxes") == 0, str(payload.get("overfull_boxes")))
    c.check("build: the PDF landed in the workspace's out/ directory",
            pdf.parent == home / "testws" / "out", str(pdf.parent))

    unknown = client.post("/build", json={"template": "nosuch", "workspace": "testws"})
    c.check("build: an unknown template is a 404",
            unknown.status_code == 404, unknown.text[:300])


def main() -> int:
    c = _Checker()
    home = _hermes_home()

    # The whole test acts on a throwaway home. Prove the override took effect
    # before anything else runs: if a Hermes helper cached the real home, the
    # rest of the suite would be touching the user's own workspaces.
    from evergreen.career import hermes_home

    previous = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home)
    try:
        c.check("isolation: HERMES_HOME points at the throwaway home",
                str(hermes_home()) == str(home), str(hermes_home()))

        router = _load_router()
        client = _client(router, f"/api/plugins/{_manifest()['name']}")

        test_the_module_loads_like_hermes_loads_it(c)
        test_workspaces(c, client)
        test_templates(c, client)
        test_generate(c, client, home)
        test_generate_refuses(c, client, home)
        test_build(c, client, home)
    finally:
        if previous is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous

    print()
    if c.failures:
        print(f"{c.failures} check(s) FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
