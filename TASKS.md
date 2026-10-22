# Evergreen — remaining work

Status as of 2026-10-22. The plugin is built, installed, and green; this is
what is left before it is genuinely shippable.

Legend: ☐ not started · ◐ in progress · ☑ done & verified

---

## 0. Settled decisions (2026-10-22)

These forks were decided and are no longer open:

| fork | decision |
|---|---|
| Where the Evergreen wiki's prose goes | **Add `career.yaml` to `evergreen/` in place.** The wiki stays where it is and *is* the prose layer. No move, no copy, no duplication. The plugin never reads notes, so `wiki/entities/` and a flat `notes/` are equivalent to it. |
| Does Evergreen become an instance of the plugin? | **Yes — migrate Evergreen to instance #1, and ship a scrubbed example workspace** in the plugin repo. A real migration is the best test of the design; the scrubbed example gives a stranger something concrete. |
| How the polish step stores its output | **A per-target overlay file plus a mechanical "no new facts" validator.** `targets/<name>/overlay.yaml` holds only the deltas; the base profile is never mutated, so a target is re-renderable and re-targetable. The validator compares a reworded bullet's content words against the original and refuses new ones. |
| Does the plugin read `notes/`? | **Write-only for now.** The plugin stores and lists notes but never reads them into a build, so the render path stays deterministic and single-input. Reading notes as polish context is the natural later upgrade. |
| How the matcher gets smarter | **Better term extraction plus a small shipped synonym table.** Weight terms by their position in the posting, extract noun phrases, and ship an editable equivalence table. No dependencies, and the deterministic-selection property is preserved. |
| Publication shape | **A public repo** (`Bryan-Pawlowski/evergreen`). The plugin dir ships zero personal data; the Evergreen *workspace* stays on this machine and is never committed. |

---

## 1. Ship the repository

- ☑ **git init + first commit.** Repo created at `D:/AI_Scratch/evergreen-plugin`
  on branch `main`, with a `.gitignore` (Python bytecode, LaTeX
  intermediates, an accidental root-level workspace, secrets, editor cruft),
  a `.gitattributes` (LF in the store, native on checkout; the vendored
  fonts and class files marked binary), and an MIT `LICENSE`.
- ☑ **A leak check that runs.** `scripts/check_no_user_data.py` scans
  tracked files for emails, phone numbers, and absolute local paths. Wired
  as a pre-commit hook via `core.hooksPath .githooks` and to CI. It is
  verified to pass on the real repo *and* to fail on a planted leak.
- ☑ **A manifest check that runs.** `scripts/check_manifest.py` is a
  self-contained subset of Hermes' `plugins validate` checks — required
  fields, semver, the `requires_hermes` spec, the known-field list, and
  the declared-vs-registered tool comparison. CI depends on no particular
  Hermes version, because the PyPI release (0.19.0) lags the git install
  and has no `validate` subcommand.
- ☑ **Pushed to GitHub** — `https://github.com/Bryan-Pawlowski/evergreen`
  (public). The remote is clean and `main` tracks `origin/main`.
- ☑ **The real consumer path works.** `hermes plugins install
  https://github.com/Bryan-Pawlowski/evergreen` clones and installs; the
  installed copy passes the selftest. This was tested after removing the
  hand-copied local install, so it is the genuine git-URL path.
- ☑ **CI is green** on GitHub Actions: the `verify` job (selftest +
  manifest check + leak check) and the `negative-tests` job (plant a leak,
  plant a broken manifest, assert both are rejected).
- ☑ **Added `templates/minimal/LICENSE.md`** — a note pointing at
  `templates/LICENSE-FONTS.md` (the minimal template uses no third-party
  class, only the vendored fonts).
- ☐ **Run `hermes plugins validate` at release time** — the authoritative
  gate, against the local Hermes 0.22.3. It passes today; CI uses the
  self-contained subset instead because the PyPI Hermes is too old to
  have it.
- ☐ **Submit to the plugin catalog** — a PR to
  `NousResearch/hermes-agent` adding `plugin-catalog/evergreen.yaml` with
  the reviewed commit SHA. Until then, install is by git URL only.

## 2. Fix a wrong claim in the existing Evergreen skill

- ☑ **The `evergreen-resume` skill said the primary CV is "LuaTeX via
  tectonic". It is not.** Tectonic 0.17 is XeTeX-based: there is no
  `--xetex` / `--lualatex` / `--pdflatex` flag (passing one is an
  "unexpected argument" error), and the default `--pass default` runs
  XeTeX + xdvipdfmx. The skill's own `resume/cv.log` says XeTeX.
  Fixed in three places: the skill's variant table and build section
  (with the reason spelled out so a future session does not re-add a
  flag), `RESUME_README.md`, and the `% !TEX TS-program` header in
  `resume/cv.tex` (now `xelatex`, with a note that tectonic ignores it
  anyway). Evergreen's `cv.pdf` was rebuilt and still compiles.
  The plugin's `skills/evergreen-cvcraft/SKILL.md` already documents the
  truth for the new codebase.

## 3. Migrate Evergreen to the new format

- ☑ **Decided where the wiki's prose goes: add `career.yaml` to
  `evergreen/` in place.** The wiki stays where it is and *is* the prose
  layer. No move, no copy, no duplication. The plugin never reads notes
  (decision above), so `wiki/entities/` and a flat `notes/` are equivalent
  to it. Consequence: the plugin's README must say "notes/ is
  conventional, not required" — a workspace is any directory with a
  `career.yaml`.
- ☐ **Build `career.yaml`** from `wiki/` + `raw/resume/resume-2026-10-20.md`.
  Watch the CV-view rule: `paid-local-llm-platform` is `cv_excluded: true`
  and must not appear.
- ☐ **Render all three variants and compare against the current PDFs**
  (`resume/cv.pdf`, `cv-altacv.pdf`, `cv-minimal.pdf`). The new ones will
  not be byte-identical — the new templates are ports, not copies — so the
  comparison is about *content parity*, not pixel parity.
- ☐ **Decide Evergreen's own future.** Does the old `resume/` directory
  become a view like the new one, or does it get retired? If Evergreen
  stays as instance #1, it should use the plugin, not a parallel
  hand-maintained set of `.tex` files.
- ☐ **Ship a scrubbed example workspace** in the plugin repo (decision
  above). It must carry **no** real personal data — the leak check is the
  gate, and it will flag anything that slips through.

## 4. Fill the gaps the design promised

- ☐ **The polish step.** Match-then-polish is half-built: `career_match`
  does the selection, but nothing rewrites the wording. Per the decision
  above: the reworded bullets go into a **per-target overlay**
  (`targets/<name>/overlay.yaml`) holding only the deltas — never into the
  base profile — and a **validator** mechanically compares a reworded
  bullet's content words against the original and refuses new ones. The
  validator is the interesting part: a synonym is not a new fact, but a
  new technology claim is.
- ☐ **The `notes/` layer.** The workspace creates `notes/` but nothing
  reads or writes it. It needs a tool (write a note, list notes) and a
  workflow in the career skill. Per the decision above, it is **write-only
  for now** — the plugin stores and lists notes but never reads them into
  a build, so the render path stays deterministic and single-input. Reading
  notes as polish context is the natural later upgrade.
- ☐ **The intake checklist is not resumable in practice.** `career_init`
  seeds `.evergreen/intake.json` and `workspace.next_question` exists,
  but the *tool* to mark an item done and get the next question is
  missing. Without it, every session re-asks everything.
- ☐ **The matcher gets smarter: better term extraction plus a small
  shipped synonym table** (decided above). Two parts:
  1. **Extraction** — weight terms by their *position* in the posting (a
     "Requirements:" block counts more than the company preamble),
     extract noun phrases rather than bare words, and stop treating
     generic tech vocabulary as noise.
  2. **A synonym table** shipped with the plugin and editable by the user
     (shipping↔delivery, CI↔continuous integration, perf↔performance).
     No dependencies, and the deterministic-selection property is
     preserved. A full embedding match was considered and rejected: it
     would add a model, a provider, a privacy question (a CV is
     sensitive), and it would make "deterministic selection" a claim
     rather than a fact.
- ☐ **The `unmatched_terms` report is not surfaced anywhere useful.** It
  is the most valuable output of the matcher and it currently only lands
  in a JSON file. It should be prominent in the build output and in the
  skill's guidance.

## 5. Verification and distribution

- ☑ **CI is green** on GitHub Actions — the `verify` job (selftest +
  manifest check + leak check) and the `negative-tests` job (plant a
  leak, plant a broken manifest, assert both are rejected).
- ☑ **The git-URL install path works** from a clean state.
- ☑ **The clean-profile test passed** (2026-10-22). A throwaway profile
  `egtest` was created, the plugin was installed into it from the git URL
  (the clone copied an `evergreen/` dir, which was removed so the install
  was genuine), and a real agent turn drove `career_init` →
  `career_profile` → `career_build` to a valid one-page PDF. `/career
  status` works there too. A second turn confirmed the plugin **refuses**
  an invalid profile patch before writing anything.
- ☑ **Two real defects found by that test, both fixed:**
  - **Patch semantics.** Dicts were replaced wholesale while lists merged
    by id, so patching one field of `person` was rejected for dropping the
    object's other fields. A patch now changes only what it names.
  - **A name collision.** A Hermes home contains its own `profile.yaml`
    (the descriptor Hermes writes for its bots). The plugin used the same
    generic name, so a Hermes home looked like a workspace and
    `career_init` would create `notes/`, `raw/`, and `out/` inside it.
    The artifact is now the **career profile** in `career.yaml`, and three
    guards back it up: a directory is a workspace only when its career
    file parses as one, an unrelated `career.yaml` is never overwritten,
    and a workspace is never created inside a Hermes home.
- ☐ **Clean-*profile* test with no local state at all.** The test above
  used a cloned profile (so it had a model and auth). A profile with
  genuinely no state — no skills, no prior sessions — is still untested.
- ☐ **The `egtest` profile is kept** as a reusable harness for the next
  round of plugin testing. It has the plugin installed from the git URL
  and its model pointed at `ollama-cloud`; recreate it with
  `hermes profile create egtest --clone --no-alias` if it is ever lost.
  Delete it with `hermes profile delete egtest -y`.
- ☐ **Plugin catalog submission**: a PR to
  `NousResearch/hermes-agent` adding `plugin-catalog/evergreen.yaml` with
  the reviewed commit SHA. Until then, install is by git URL only.
- ☐ **A worked example**: a small `examples/` directory with a scrubbed
  career profile and the PDF it produces, so a stranger can see the output
  without setting anything up. This is the "scrubbed example workspace"
  from the decisions above.

## 6. Deferred / optional

- ☐ **The `plain` dialect's dates** use `\hfill` + `\mbox{}`, which is
  right but fragile. Not urgent.
- ☐ **Multi-page CVs**: nothing has been tested with content that spans
  two pages. The `page_budget` warning fires, but the layout of a
  two-page minimal CV (the sidebar is drawn on every page) is unverified.
- ☐ **A `notes/` → profile direction.** The current flow is profile →
  CV. Nothing lets a note *influence* a profile entry.
- ☐ **Windows path handling in the build**: `FONTCONFIG_FILE` is set to
  a native path, which works, but the `cwd`-relative `<dir prefix="cwd">`
  in `fonts.conf` is a known sharp edge. It is documented; it is not
  fixed.

---

## Done and verified

- ☑ Native Hermes plugin: `plugin.yaml` + `register(ctx)`, 6 tools, a
  `/career` slash command, a `hermes career` CLI subcommand, 2 bundled
  skills.
- ☑ Two-artefact split: the plugin dir holds only machinery and templates
  (swept for leaked personal data — only author attribution remains); the
  user's data lives in a workspace at a path they choose.
- ☑ Hybrid source of truth: `career.yaml` (structured, schema-validated)
  + `notes/` (reserved) + `raw/` (immutable, content-hashed).
- ☑ Deterministic match-then-polish: `career_match` ranks entries
  reproducibly and reports what the profile cannot cover.
- ☑ Extension contract: pure `.tex` + slot markers + a documented dialect
  interface; `custom` dialects supported.
- ☑ Four templates shipped (`plain`, `yaac`, `altacv`, `minimal`), each
  compiled by the selftest, each visually reviewed.
- ☑ `hermes plugins validate` passes; `hermes plugins doctor --ci` exits 0.
- ☑ Selftest: 37 core checks + 16 tool-level end-to-end checks, all green.
- ☑ Installed and enabled in the active profile.
- ☑ **A public GitHub repo** with a real `.gitignore` and `.gitattributes`,
  an MIT `LICENSE`, a pre-commit leak check, a self-contained manifest
  check, and green CI on both a positive and a negative test.
- ☑ **The consumer path is proven**: `hermes plugins install
  https://github.com/Bryan-Pawlowski/evergreen` works from a clean state,
  and the installed copy passes the selftest.
