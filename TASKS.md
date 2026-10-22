# Evergreen — remaining work

Status as of 2026-10-22. The plugin is built, installed, and green; this is
what is left before it is genuinely shippable.

Legend: ☐ not started · ◐ in progress · ☑ done & verified

---

## 0. Settled decisions (2026-10-22)

These forks were decided and are no longer open:

| fork | decision |
|---|---|
| Where the Evergreen wiki's prose goes | **Add `profile.yaml` to `evergreen/` in place.** The wiki stays where it is and *is* the prose layer. No move, no copy, no duplication. The plugin never reads notes, so `wiki/entities/` and a flat `notes/` are equivalent to it. |
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
- ☐ **Push to GitHub** and verify the real consumer path:
  `hermes plugins install owner/repo`. The local-copy install works, but
  the git-URL path is untested and it is the path a stranger will use.
- ☐ **Add `templates/minimal/LICENSE.md`** — a note pointing at
  `templates/LICENSE-FONTS.md` (the minimal template uses no third-party
  class, only the vendored fonts).
- ☐ **CI**: run `python -m evergreen.selftest`, `hermes plugins validate .`
  and the leak check on push. The selftest needs tectonic, so CI needs the
  tectonic install step.

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

- ☐ **Decide where the wiki's prose goes.** The hybrid design says
  `profile.yaml` holds facts and `notes/` holds nuance. The Evergreen wiki
  has both: `entities/*.md` carry narrative that the structured profile
  cannot (the *why* behind a project, the "AI co-driven build" arc). Three
  options:
  1. `notes/` holds the entity pages verbatim, and the profile points at
     them. Most faithful, keeps the wiki intact.
  2. The profile absorbs the CV-relevant prose; the wiki stays as a
     private knowledge base the plugin never reads.
  3. The profile absorbs everything and the wiki is retired.
  Recommend **(1)**: the plugin does not need to read the notes, so the
  wiki can stay exactly as it is and the profile simply becomes the
  render input.
- ☐ **Build `profile.yaml`** from `wiki/` + `raw/resume/resume-2026-10-20.md`.
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

## 4. Fill the gaps the design promised

- ☐ **The polish step.** Match-then-polish is half-built: `career_match`
  does the selection, but nothing rewrites the wording. The design says
  the LLM rewords *existing bullets only* — never adds a claim. This needs
  a tool that takes a match report plus reworded bullets, checks that no
  new facts appeared, and writes the result back. The "no new facts"
  check is the interesting part: compare the content words of the reworded
  bullet against the original and flag anything new.
- ☐ **The `notes/` layer.** The workspace creates `notes/` but nothing
  reads or writes it. It needs a tool (write a note, list notes, attach a
  note to a profile entry) and a workflow in the career skill.
- ☐ **The intake checklist is not resumable in practice.** `career_init`
  seeds `.evergreen/intake.json` and `workspace.next_question` exists,
  but the *tool* to mark an item done and get the next question is
  missing. Without it, every session re-asks everything.
- ☐ **Matcher synonyms.** The matcher is lexical. A bullet saying
  "shipping" will not match a posting asking for "delivery". A small
  synonym table (shipping↔delivery, CI↔continuous integration,
  perf↔performance) would help; a full embedding match would be a
  different design.
- ☐ **The `unmatched_terms` report is not surfaced anywhere useful.** It
  is the most valuable output of the matcher and it currently only lands
  in a JSON file. It should be prominent in the build output and in the
  skill's guidance.

## 5. Verification and distribution

- ☐ **CI**: run `python -m evergreen.selftest` and
  `hermes plugins validate .` on push. The selftest needs tectonic, so CI
  needs the tectonic install step.
- ☐ **Clean-profile test**: create a throwaway Hermes profile, install
  from the git URL, and run `/career init` → `/career build` with no
  local state. Everything so far has been tested in *this* profile with
  the plugin copied into place, which does not prove the install path.
- ☐ **Plugin catalog submission**: a PR to
  `NousResearch/hermes-agent` adding `plugin-catalog/evergreen.yaml` with
  the reviewed commit SHA. Until then, install is by git URL only.
- ☐ **A worked example**: a small `examples/` directory with a filled
  profile and the PDF it produces, so a stranger can see the output
  without setting anything up.

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
- ☑ Hybrid source of truth: `profile.yaml` (structured, schema-validated)
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
