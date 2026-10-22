"""End-to-end test of the core pipeline: profile -> render -> compile.

Run with the Hermes python (the one that has ruamel.yaml):

    python -m evergreen.selftest
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from . import markup, matcher
from .career import Workspace, validate
from .render import discover_templates, load_template, render_tex, slots_in, stage

SAMPLE = {
    "schema_version": 1,
    "person": {
        "name": {"first": "Test", "last": "Person"},
        "tagline": "Software Engineer & Systems Builder",
        "summary": "Engineer with 10 years across _rendering_ and **infrastructure**.",
        "email": "test@example.com",
        "phone": "+1 555 0100",
        "location": "Portland, Oregon, USA",
        "links": [
            {"label": "testuser", "url": "https://github.com/testuser", "icon": "github"},
        ],
    },
    "experience": [
        {
            "id": "acme-senior",
            "role": "Senior Software Engineer",
            "org": "Acme Corp",
            "location": "Remote",
            "start": "Mar 2020",
            "end": "Present",
            "bullets": [
                {"text": "Built a **CUDA** rendering pipeline that cut frame time 40%."},
                {"text": "Led migration of the CI system to _GitHub Actions_."},
            ],
            "tags": ["CUDA", "C++", "CI/CD"],
            "priority": 80,
        },
        {
            "id": "beta-junior",
            "role": "Junior Developer",
            "org": "Beta Inc",
            "start": "Jun 2017",
            "end": "Feb 2020",
            "bullets": [
                {"text": "Wrote Python ETL scripts and fixed 100+ production bugs."},
            ],
            "tags": ["Python"],
            "priority": 20,
        },
    ],
    "projects": [
        {
            "id": "toolkit",
            "title": "Toolkit",
            "subtitle": "Open source CLI",
            "date": "2023",
            "bullets": [{"text": "1.2k stars on [GitHub](https://github.com/testuser/toolkit)."}],
        },
    ],
    "education": [
        {
            "id": "state",
            "degree": "B.Sc. Computer Science",
            "org": "State University",
            "start": "2013",
            "end": "2017",
            "detail": "Focus on **graphics** and high-performance computing.",
        },
    ],
    "skills": [
        {"label": "Languages", "items": ["C++", "Python", "Rust"]},
        {"label": "Systems", "items": ["CUDA", "Linux", "Docker"]},
    ],
    "milestones": [
        {"title": "Shipped the renderer", "date": "2024", "detail": "Live in 40 games", "icon": "trophy"},
    ],
    "languages": [{"name": "English", "level": "Native"}],
    "sections": [],
}

JD = """
We are hiring a Senior Software Engineer to own our CUDA rendering pipeline.

Requirements:
- 5+ years of C++ and GPU programming (CUDA, OpenGL or Vulkan)
- Strong CI/CD experience (GitHub Actions or similar)
- Experience with Docker and Linux
- Python for tooling
- Nice to have: Vulkan, performance profiling
"""


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def main() -> int:
    failures = 0

    # -- markup ----------------------------------------------------------
    failures += not check(
        "markup: bold",
        markup.inline("a **bold** word") == r"a \textbf{bold} word",
        markup.inline("a **bold** word"),
    )
    failures += not check(
        "markup: italic",
        markup.inline("an _italic_ word") == r"an \textit{italic} word",
        markup.inline("an _italic_ word"),
    )
    failures += not check(
        "markup: link",
        markup.inline("[site](http://x.com)") == r"\href{http://x.com}{site}",
        markup.inline("[site](http://x.com)"),
    )
    failures += not check(
        "markup: escapes specials",
        markup.inline("100% & $5") == r"100\% \& \$5",
        markup.inline("100% & $5"),
    )
    failures += not check(
        "markup: nested bold+italic",
        markup.inline("**bold _both_**") == r"\textbf{bold \textit{both}}",
        markup.inline("**bold _both_**"),
    )
    failures += not check(
        "markup: underscore in word is literal",
        markup.inline("snake_case_name") == r"snake\_case\_name",
        markup.inline("snake_case_name"),
    )
    failures += not check(
        "markup: en dash becomes --",
        markup.inline("10–12") == "10--12",
        markup.inline("10–12"),
    )

    # -- profile ---------------------------------------------------------
    errors = validate(SAMPLE)
    failures += not check("profile: sample validates", not errors, "; ".join(errors))

    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(Path(tmp) / "career")
        ws.create()
        ws.career_path.write_text(
            json.dumps(SAMPLE, indent=2), encoding="utf-8"
        )
        profile = ws.load()
        failures += not check("profile: loads from disk", profile.person["name"]["first"] == "Test")
        failures += not check("profile: visible filters hidden", len(profile.visible("experience")) == 2)

        # -- matcher ----------------------------------------------------
        report = matcher.match(profile, JD)
        top = report.by_slot["experience"][0]
        failures += not check(
            "matcher: CUDA role ranks first",
            top.entry_id == "acme-senior",
            f"got {top.entry_id} ({top.title})",
        )
        failures += not check(
            "matcher: reports matched terms",
            "cuda" in top.matched_terms,
            str(top.matched_terms),
        )
        failures += not check(
            "matcher: reports unmatched terms",
            "vulkan" in report.unmatched_terms,
            str(report.unmatched_terms),
        )
        failures += not check(
            "matcher: no trailing punctuation in terms",
            not any(t.endswith((".", ",", ";")) for t in report.jd_terms),
            str([t for t in report.jd_terms if t.endswith((".", ",", ";"))]),
        )
        failures += not check(
            "matcher: posting filler is not a term",
            "hiring" not in report.jd_terms,
            "hiring" in report.jd_terms,
        )
        failures += not check(
            "matcher: report carries the entry",
            bool(report.matched[0].to_dict().get("entry")),
            "the entry is missing from the serialized report",
        )

        # -- render -----------------------------------------------------
        shipped = Path(__file__).resolve().parent.parent / "templates"
        templates = discover_templates(shipped)
        failures += not check(
            "render: templates discovered",
            {t.id for t in templates} >= {"plain"},
            str([t.id for t in templates]),
        )

        template = load_template(shipped / "plain")
        failures += not check(
            "render: plain template uses slots",
            "experience" in slots_in(template),
            str(slots_in(template)),
        )

        tex = render_tex(template, profile)
        failures += not check("render: no slot markers survive", "%HERMES" not in tex)
        failures += not check("render: content present", "Acme Corp" in tex)
        failures += not check("render: markup converted", r"\textbf{CUDA}" in tex)
        # A percent in the *content* must be escaped. The template's own
        # LaTeX comments legitimately contain %, so check the rendered body
        # rather than the whole file — and look for an UNESCAPED percent, not
        # any percent at all (a correctly escaped \% is still a %).
        body = tex.split(r"\begin{document}", 1)[-1]
        unescaped = re.findall(r"(?<!\\)%", body)
        failures += not check(
            "render: content percent is escaped",
            not unescaped,
            f"{len(unescaped)} unescaped % in: "
            + str([ln for ln in body.splitlines() if re.search(r"(?<!\\)%", ln)][:3]),
        )

        with tempfile.TemporaryDirectory() as build:
            main_tex = stage(template, Path(build))
            main_tex.write_text(tex, encoding="utf-8", newline="\n")
            from .build import build_tex, find_tectonic
            if not find_tectonic():
                print("[SKIP] compile — tectonic not installed")
            else:
                result = build_tex(main_tex, Path(build), engine=template.engine)
                failures += not check(
                    "build: compiles to PDF",
                    result.ok,
                    "; ".join(result.errors)[:400],
                )
                failures += not check(
                    "build: one page",
                    result.pages == 1,
                    f"pages={result.pages}",
                )
                failures += not check(
                    "build: no overfull boxes",
                    result.overfull == 0,
                    f"overfull={result.overfull}",
                )

        # -- every shipped template renders AND compiles ---------------
        for t in templates:
            if t.id == "plain":
                continue
            t_tex = render_tex(t, profile)
            failures += not check(
                f"render[{t.id}]: no slot markers survive",
                "%HERMES" not in t_tex,
            )
            failures += not check(
                f"render[{t.id}]: content present",
                "Acme Corp" in t_tex,
            )
            # Every field the profile carries must reach the rendered
            # LaTeX. The minimal dialect used to drop a language entry's
            # level, so the list of languages silently vanished from the
            # PDF while the profile still said it was there.
            failures += not check(
                f"render[{t.id}]: the language level is rendered",
                "Native" in t_tex,
                "the language level was dropped",
            )
            failures += not check(
                f"render[{t.id}]: the language name is rendered",
                "English" in t_tex,
            )
            if not find_tectonic():
                continue
            with tempfile.TemporaryDirectory() as build:
                main_tex = stage(t, Path(build))
                main_tex.write_text(t_tex, encoding="utf-8", newline="\n")
                fc = None
                if t.fontconfig and (Path(build) / t.fontconfig).exists():
                    fc = str(Path(build) / t.fontconfig)
                r = build_tex(main_tex, Path(build), engine=t.engine, fontconfig=fc)
                failures += not check(
                    f"build[{t.id}]: compiles to PDF",
                    r.ok,
                    "; ".join(r.errors)[:600],
                )
                failures += not check(
                    f"build[{t.id}]: has pages",
                    r.pages is not None and r.pages >= 1,
                    f"pages={r.pages}",
                )

    print()
    if failures:
        print(f"{failures} check(s) FAILED")
        return 1
    print("all checks passed")
    return 0


def test_tools_end_to_end() -> int:
    """Exercise the tool interface the way an agent would.

    This path is the one that broke: a targeted build needs the match report
    to carry its entries, and the build must copy the PDF out of the work
    directory before the work directory is removed.
    """
    import json as _json

    from . import (career_build, career_init, career_ingest, career_list,
                   career_match, career_profile)

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        # The workspace now lives directly under the Hermes home, so the
        # test needs its own home - otherwise it writes into the real one.
        fake_home = Path(tmp) / "hermes"
        fake_home.mkdir()
        previous_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(fake_home)
        try:
            failures += _run_tool_checks(fake_home)
        finally:
            if previous_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = previous_home
    return failures


def _run_tool_checks(fake_home: Path) -> int:
    """The tool-level end-to-end checks, against an isolated Hermes home."""
    import json as _json

    from . import (career_build, career_init, career_ingest, career_list,
                   career_match, career_profile)

    failures = 0

    def check(label, condition, detail=""):
        nonlocal failures
        ok = bool(condition)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}"
              + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            failures += 1
        return ok

    ws_name = "career"
    ws = str(fake_home / ws_name)

    out = _json.loads(career_init({"name": ws_name}))
    check("tools: init creates a workspace", out.get("ok"), str(out)[:200])
    check("tools: init returns the first question",
          bool(out.get("next_question")))
    check("tools: init places the workspace under the Hermes home",
          out.get("path") == ws, f"{out.get('path')} != {ws}")
    check("tools: init records the name in the career profile",
          "name: career" in (Path(ws) / "career.yaml").read_text(encoding="utf-8"))

    out = _json.loads(career_list({}))
    check("tools: list finds the workspace",
          any(w["name"] == ws_name for w in out.get("workspaces", [])),
          str(out)[:200])
    check("tools: list reports the profile status",
          any(w.get("profile_status") == "valid"
              for w in out.get("workspaces", [])),
          str(out)[:200])

    # A name that is not a safe path segment must be refused.
    out = _json.loads(career_init({"name": "bad name!"}))
    check("tools: an invalid name is refused", "error" in out, str(out)[:200])
    out = _json.loads(career_init({"name": "../escape"}))
    check("tools: a path traversal name is refused", "error" in out,
          str(out)[:200])

    out = _json.loads(career_ingest({
        "text": "Jane Doe, Senior Engineer. Built a CUDA renderer.",
        "source": "https://example.com/cv", "kind": "resume",
        "workspace": ws,
    }))
    check("tools: ingest stores a source", out.get("status") == "ingested",
          str(out)[:200])
    check("tools: ingest returns a hash", len(out.get("sha256", "")) == 64)

    # Re-ingesting identical content must be a no-op, not a second file.
    out2 = _json.loads(career_ingest({
        "text": "Jane Doe, Senior Engineer. Built a CUDA renderer.",
        "source": "https://example.com/cv", "kind": "resume",
        "workspace": ws,
    }))
    check("tools: re-ingest is a no-op",
          out2.get("status") == "unchanged", str(out2)[:200])

    # A changed body must be stored as a NEW document, not overwrite.
    out3 = _json.loads(career_ingest({
        "text": "Jane Doe, Senior Engineer. Built a CUDA renderer. Now V2.",
        "source": "https://example.com/cv", "kind": "resume",
        "workspace": ws,
    }))
    check("tools: changed source becomes a new document",
          out3.get("status") == "ingested"
          and out3.get("path") != out.get("path"),
          str(out3)[:200])

    # The stored hashes must verify — a mismatch here means the hash and
    # the written body disagree.
    from .workspace import verify_raw
    from .career import Workspace as _WS
    drift = [r for r in verify_raw(_WS(Path(ws))) if r["status"] != "ok"]
    check("tools: raw hashes verify (no phantom drift)", not drift,
          str(drift)[:300])

    patch = {
        "person": {
            "name": {"first": "Jane", "last": "Doe"},
            "tagline": "Senior Graphics Engineer",
            "summary": "Engineer who ships rendering systems.",
            "email": "jane@example.com",
            "location": "Portland, Oregon, USA",
            "links": [{"label": "janedoe",
                       "url": "https://github.com/janedoe",
                       "icon": "github"}],
        },
        "experience": [{
            "id": "acme", "role": "Senior Engineer", "org": "Acme Corp",
            "location": "Remote", "start": "Mar 2020", "end": "Present",
            "bullets": [{"text": "Built a **CUDA** rendering pipeline."}],
            "tags": ["CUDA", "C++"], "priority": 90,
        }],
        "projects": [{"id": "tool", "title": "Toolkit", "date": "2023"}],
        "education": [{"id": "state", "degree": "B.Sc. Computer Science",
                       "org": "State University"}],
        "skills": [{"label": "Languages", "items": ["C++", "Python"]}],
        "milestones": [{"title": "Shipped it", "date": "2024",
                        "icon": "trophy"}],
    }
    out = _json.loads(career_profile({
        "action": "update", "patch": patch, "workspace": ws}))
    check("tools: profile update succeeds", out.get("ok"), str(out)[:300])

    out = _json.loads(career_profile({
        "action": "read", "workspace": ws}))
    check("tools: profile read returns the data",
          out.get("career_profile", {}).get("person", {}).get("name", {}).get("first")
          == "Jane", str(out)[:200])

    # An invalid patch must be rejected, not written. Note this is a
    # TYPE error, not a missing field: since patches now merge, a
    # patch that omits a required field no longer invalidates the
    # profile — it just leaves the field alone.
    out = _json.loads(career_profile({
        "action": "update", "workspace": ws,
        "patch": {"person": {"name": {"first": 123, "last": "Doe"}}}}))
    check("tools: invalid patch is rejected", "error" in out, str(out)[:200])
    check("tools: an invalid patch says what is wrong",
          bool(_json.loads(career_profile({
              "action": "update", "workspace": ws,
              "patch": {"person": {"name": {"first": 123, "last": "Doe"}}}}))
              .get("details")),
          "the error carried no details")

    # A patch must mean "change what I named". Patching one field of
    # `person` must not drop the other fields — the whole-object
    # replacement this used to do made every patch a full rewrite, and
    # silently dropped required fields.
    out = _json.loads(career_profile({
        "action": "update", "workspace": ws,
        "patch": {"person": {"tagline": "Senior Graphics Engineer"}}}))
    check("tools: a partial person patch succeeds", out.get("ok"),
          str(out)[:300])
    person = _json.loads(career_profile({
        "action": "read", "workspace": ws}))["career_profile"]["person"]
    check("tools: a partial patch changes only what it names",
          person.get("tagline") == "Senior Graphics Engineer"
          and person.get("name", {}).get("first") == "Jane"
          and person.get("email") == "jane@example.com",
          str(person)[:300])

    # A list of records merges by id; a list of values replaces.
    out = _json.loads(career_profile({
        "action": "update", "workspace": ws,
        "patch": {"experience": [{"id": "acme", "role": "Staff Engineer"}]}}))
    check("tools: an id-matched entry merges", out.get("ok"), str(out)[:200])
    entries = _json.loads(career_profile({
        "action": "read", "workspace": ws}))["career_profile"]["experience"]
    check("tools: merging an entry keeps the other fields",
          len(entries) == 1
          and entries[0]["role"] == "Staff Engineer"
          and entries[0]["org"] == "Acme Corp",
          str(entries)[:300])

    out = _json.loads(career_profile({
        "action": "update", "workspace": ws,
        "patch": {"skills": [{"label": "Only", "items": ["Rust"]}]}}))
    entries = _json.loads(career_profile({
        "action": "read", "workspace": ws}))["career_profile"]["skills"]
    check("tools: an id-less list is replaced, not appended",
          len(entries) == 1 and entries[0]["label"] == "Only",
          str(entries)[:200])

    # An unrelated file must not be mistaken for a career profile, and
    # must not be overwritten.
    foreign = fake_home / "foreign"
    foreign.mkdir()
    (foreign / "career.yaml").write_text(
        "name: someone\ndescription: not a career profile\n", encoding="utf-8")
    out = _json.loads(career_init({"name": "foreign"}))
    check("tools: a foreign career.yaml is not a workspace",
          "error" in out, str(out)[:200])
    check("tools: a foreign career.yaml is not overwritten",
          (foreign / "career.yaml").read_text(encoding="utf-8")
          .startswith("name: someone"))

    # The Hermes home itself must never be treated as a workspace.
    from .career import Workspace as _WS2
    check("tools: a Hermes home is not a workspace",
          not _WS2(fake_home).exists())

    # A name that collides with something Hermes owns must be refused.
    (fake_home / "plugins").mkdir()
    out = _json.loads(career_init({"name": "plugins"}))
    check("tools: a name colliding with a Hermes directory is refused",
          "error" in out, str(out)[:200])
    check("tools: the colliding directory was not touched",
          (fake_home / "plugins").is_dir()
          and not (fake_home / "plugins" / "career.yaml").exists())

    # The Hermes home must not be polluted with workspace files.
    check("tools: nothing was written directly into the Hermes home",
          not (fake_home / "career.yaml").exists()
          and not (fake_home / "notes").exists())

    # With no name, no configured path, and no walk-up hit, the resolver
    # must land on a workspace that exists rather than on a stale path.
    # Do this before adding the "broken" fixture, so there is exactly one
    # workspace to resolve to.
    from . import _resolve_workspace
    resolved = _resolve_workspace(None)
    check("tools: the resolver finds the single workspace by default",
          str(resolved.root) == ws, str(resolved.root))
    resolved = _resolve_workspace(None, name=ws_name)
    check("tools: the resolver finds a workspace by name",
          str(resolved.root) == ws, str(resolved.root))

    # A workspace whose profile does not validate must still be listed.
    # Hiding it would make the tool that exists to FIND workspaces unable
    # to find one that is merely unpolished - and the caller needs to know
    # it exists in order to repair it.
    broken = fake_home / "broken"
    broken.mkdir()
    (broken / "career.yaml").write_text(
        "schema_version: 1\n"
        "name: broken\n"
        "person:\n"
        "  name:\n"
        "    first: 123\n"
        "    last: Doe\n"
        "  tagline: ''\n",
        encoding="utf-8")
    out = _json.loads(career_list({}))
    listed = {w["name"]: w for w in out.get("workspaces", [])}
    check("tools: an invalid workspace is still listed",
          "broken" in listed, str(sorted(listed)))
    check("tools: an invalid workspace is flagged as invalid",
          listed.get("broken", {}).get("profile_status") == "invalid",
          str(listed.get("broken"))[:200])
    check("tools: an invalid workspace explains itself",
          bool(listed.get("broken", {}).get("profile_error")),
          str(listed.get("broken"))[:200])


    jd = ("We are hiring a CUDA rendering engineer. Requirements: C++, "
          "GPU programming, Docker, Python. Nice to have: Vulkan.")
    out = _json.loads(career_match({
        "job_description": jd, "target": "acme", "workspace": ws}))
    check("tools: match succeeds", out.get("ok"), str(out)[:200])
    check("tools: match reports unmatched terms",
          "vulkan" in out["report"]["unmatched_terms"],
          str(out["report"]["unmatched_terms"]))

    out = _json.loads(career_build({
        "target": "acme", "template": "yaac", "workspace": ws}))
    check("tools: targeted build succeeds", out.get("ok"), str(out)[:400])
    check("tools: targeted build produces a PDF",
          bool(out.get("pdf")) and Path(out["pdf"]).exists(),
          str(out.get("pdf")))
    check("tools: targeted build has pages",
          (out.get("pages") or 0) >= 1, str(out.get("pages")))

    # The untargeted build must work too.
    out = _json.loads(career_build({
        "template": "plain", "workspace": ws}))
    check("tools: untargeted build succeeds", out.get("ok"),
          str(out)[:300])

    print()
    if failures:
        print(f"{failures} tool check(s) FAILED")
        return 1
    print("all tool checks passed")
    return 0


def test_cli_surface() -> int:
    """The `hermes career ...` subcommand must match the tool surface.

    The CLI is wired separately from the tools, so it can drift: the tools
    moved to name-based workspaces and the CLI kept the old path argument.
    """
    import argparse
    import json as _json
    import io
    import contextlib

    from . import career_cli, setup_cli

    failures = 0

    def check(label, condition, detail=""):
        nonlocal failures
        ok = bool(condition)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}"
              + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            failures += 1
        return ok

    # Hermes builds the command like this: it creates the ArgumentParser
    # for the command, hands it to setup_fn, and calls the handler with
    # the parsed namespace. Build it the same way here, so the test
    # exercises the real contract rather than a convenient imitation.
    parser = argparse.ArgumentParser(prog="hermes career")
    setup_cli(parser)

    # The subcommands the README and the chat command both promise.
    sub_action = next(a for a in parser._actions
                      if isinstance(a, argparse._SubParsersAction))
    names = set(sub_action.choices)
    check("cli: the subcommands are present",
          {"init", "list", "status", "templates", "match", "build"} <= names,
          str(sorted(names)))

    # `init` takes a name, not a path.
    init_parser = sub_action.choices["init"]
    init_positional = [a.dest for a in init_parser._actions
                       if not a.option_strings]
    check("cli: init takes a name argument", init_positional == ["name"],
          str(init_positional))
    check("cli: init no longer takes a path argument",
          "path" not in init_positional)

    # The handler must be reachable the way Hermes reaches it: parse the
    # argv, then call the namespace' func.
    ns = parser.parse_args(["init", "demo"])
    check("cli: the parsed namespace routes to the handler",
          getattr(ns, "func", None) is career_cli,
          str(getattr(ns, "func", None)))
    check("cli: init parses the workspace name",
          getattr(ns, "name", None) == "demo",
          str(getattr(ns, "name", None)))
    check("cli: the subcommand is on the namespace",
          getattr(ns, "evergreen_command", None) == "init",
          str(getattr(ns, "evergreen_command", None)))

    # An unknown subcommand must not crash the CLI. argparse rejects it
    # itself with exit code 2 and its own usage message, which is the
    # right behaviour — the plugin handler is never reached.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                parser.parse_args(["__nope__"])
                code = 0
            except SystemExit as exc:
                code = exc.code
    check("cli: an unknown subcommand is rejected cleanly",
          code == 2, f"exit code {code!r}")
    check("cli: an unknown subcommand prints a usage message",
          "usage" in err.getvalue().lower(),
          err.getvalue()[:120])

    # With no subcommand at all, the handler runs and prints the usage.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ns = parser.parse_args([])
        code = ns.func(ns)
    check("cli: no subcommand prints the usage and exits non-zero",
          code == 1 and "hermes career" in buf.getvalue(),
          f"code={code!r} out={buf.getvalue()[:80]!r}")

    print()
    if failures:
        print(f"{failures} CLI check(s) FAILED")
        return 1
    print("all CLI checks passed")
    return 0


def test_schema_matches_handlers() -> int:
    """Every declared tool parameter must be one the handler reads.

    The tool schemas and the handlers are written separately, and a
    parameter that drifts out of one of them is invisible until an agent
    calls the tool and gets an error it cannot diagnose. The `career_init`
    schema once still declared `path` after the handler had moved to
    `name`; the agent had to work around it.
    """
    import inspect

    from . import TOOLS

    failures = 0

    def check(label, condition, detail=""):
        nonlocal failures
        ok = bool(condition)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}"
              + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            failures += 1
        return ok

    for tool in TOOLS:
        name = tool["name"]
        declared = set(tool["parameters"].get("properties", {}))
        required = set(tool["parameters"].get("required", []))

        source = inspect.getsource(tool["handler"])
        read = set(re.findall(r'params\.get\(\s*"([a-z_]+)"', source))

        # A declared parameter the handler never reads is dead weight the
        # model will fill in and the handler will ignore.
        unread = declared - read
        check(f"schema[{name}]: every declared parameter is read",
              not unread, f"never read: {sorted(unread)}")

        # A required parameter the handler does not read is a trap: the
        # model is required to send something that goes nowhere.
        check(f"schema[{name}]: no required parameter is unread",
              not (required & unread), f"unread but required: {sorted(required & unread)}")

        # Required parameters must exist in the schema.
        check(f"schema[{name}]: every required parameter is declared",
              required <= declared, f"undeclared: {sorted(required - declared)}")

    # The tools that resolve a workspace must all accept a name, since
    # the name is how a workspace is addressed.
    for tool in TOOLS:
        if tool["name"] in {"career_init", "career_list"}:
            continue
        declared = set(tool["parameters"].get("properties", {}))
        check(f"schema[{tool['name']}]: accepts a workspace name",
              "name" in declared, str(sorted(declared)))

    print()
    if failures:
        print(f"{failures} schema check(s) FAILED")
        return 1
    print("all schema checks passed")
    return 0


if __name__ == "__main__":
    rc = main()
    if rc == 0:
        rc = test_tools_end_to_end()
    if rc == 0:
        rc = test_cli_surface()
    if rc == 0:
        rc = test_schema_matches_handlers()
    sys.exit(rc)
