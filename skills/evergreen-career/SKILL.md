---
name: evergreen-career
description: "Use when building or updating someone's career workspace: intake, profile, job-description matching, CV generation."
version: 0.1.0
author: "Bryan-Pawlowski"
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [career, cv, resume, latex, profile]
    category: productivity
---

# Evergreen — career workspace workflow

A **workspace** holds one person's career data. The **career profile**
(`career.yaml`) is the structured source of truth; `notes/` holds prose
nuance; `raw/` holds immutable ingested sources; `targets/` holds per-job
match reports; `out/` holds rendered PDFs.

The rendered CV is a **view** over the career profile, not the source of
truth. A change to what a CV shows is a change to the profile (or to a
`visible: false` flag), never an edit to the generated `.tex`.

## When to Use

- The user wants to build or update a CV, resume, or career profile.
- The user hands you a job description and wants a tailored CV.
- The user asks you to ingest a CV, repo, or project page into their career
  record.
- The user asks how their career looks, or what their CV says.

## The shape of the work

### 1. Intake (once per workspace)

`career_init <name>` creates the workspace and returns the first question.
The name becomes a directory directly under the Hermes home (one level
deeper than the home itself), so `career_init jane` creates
`<hermes home>/jane/`. The name is recorded in the career profile, so it
travels with the data if the workspace is moved or copied. Use
`career_list` to see the workspaces that exist.

Walk the checklist; it is ordered, because early answers inform later ones:

1. **Name and headline** — the name on the CV, and the one-line role summary.
2. **Professional summary** — two or three sentences: what they do, what they
   are known for.
3. **Existing CV or resume** — the highest-value source. Ingest it with
   `career_ingest` (store the document), then read it and derive profile
   entries from it.
4. **GitHub account or repositories** — public repos can be read directly; for
   private ones, ask for a local clone path.
5. **Links to work** — store pages, project sites, articles, talks.
6. **Work history** — title, employer, dates, and above all *what shipped*.
   Push for specifics: a bullet that names a shipped product, a measured
   number, or a named system is worth ten that list responsibilities.
7. **Selected projects** — which to show, which to keep private.
8. **Skills** — grouped the way they want them shown.
9. **Education** — degree, school, dates, anything worth highlighting.

**Ask about skills, do not assume them.** When a source implies a skill the
user has not claimed, ask. A CV is a claim about what someone can do, and
the user is the only authority on that.

**Ingest sources, do not paraphrase from memory.** Anything the user provides
goes through `career_ingest` first, so the raw layer holds the original
document. The raw layer is immutable: re-ingesting identical content is a
no-op, and a corrected source is a *new* document, not an edit.

### 2. Write the career profile

`career_profile` with `action: "update"` and a patch. A patch changes
only what it names: nested objects merge key by key, and lists of records
(experience, projects, education, milestones) merge by `id`, so you can
update one entry without resending the rest. Lists of values (skills,
languages, links) are replaced.

**Prose style for bullets.** The house style for the career profile:

- **No em-dashes in prose.** A dash *between two facts in a sentence* goes; a
  dash *inside a range* stays (`10–12` is a range, `DirectX 10–12` is a
  range, `shipped – and it worked` is prose). Substitute a comma (appositive),
  a colon (result or lead-in), parentheses, or a rewritten clause.
- **Concise but still human.** Keep the concrete nouns (`Unreal Editor
  builds`, `Steam depots`, `RenderDoc`, `~5–7 min`) and compress *around*
  them. Prefer the plainer verb: "Led software design & architecture of" →
  "Led design & architecture of".
- **"Concise" means shorter sentences, not telegraphic fragments.**

**Inline markup, not raw LaTeX.** Profile text supports `**bold**`,
`_italic_`, `` `code` ``, and `[label](url)`. Everything else is escaped
automatically. Never put LaTeX in a profile field: it would not survive a
change of template, and it would let a profile inject arbitrary macros.

**Verify attribution on every project bullet.** A bullet that names a
*capability* reads as a claim that the author built that capability, even
when the project page says otherwise. Before writing a bullet, establish who
owns which layer — `git log` authorship, SDK vs project directories, the
project's own comments — and name the contribution explicitly.

**Do not claim a capability the user's stack does not have.** If a bullet
would imply you built something a framework or SDK provided, either reword to
name the actual layer, or leave it out.

### 3. Match a job description

`career_match` with the job description (text or a file path) and a `target`
name. The match is **deterministic**: it extracts the job's terms, weights
them, and ranks the career profile's entries by how much of the job they cover.

It returns:
- `matched` — each entry with its score and the terms it matched.
- `required_terms` — the job's most significant terms.
- `unmatched_terms` — the job's terms that **nothing** in the profile covers.

**`unmatched_terms` is the most important output.** It tells you what the user
cannot honestly claim. Report it. Do not paper over it by inventing
experience, and do not silently drop the requirement from the CV — let the
user decide whether they have relevant experience the profile is missing, or
whether this is the wrong job.

**Tailoring is selection, then wording.** In that order:
1. Selection is the matcher's output — which entries appear, and which are
   dropped as irrelevant.
2. Wording is the polish step, and it only **rewrites existing bullets**. A
   reworded bullet must keep every fact it had; it may only re-aim the
   emphasis at the job's language. Never add a claim, never add a number
   that was not in the source, never add a technology the user did not use.

### 4. Build

`career_build` with an optional `template` and `target`. It renders the
profile into LaTeX, compiles with tectonic, and returns the PDF path, the
page count, and the overfull-box count.

Then:
- **Check the page count** against the template's budget. A CV that runs to
  three pages when the template budgets two is a content problem, not a
  typography problem — trim by merging bullets, not by shrinking type.
- **Check the overfull count.** An overfull hbox means a line runs past its
  column; a word past the page edge is clipped. Fix the cause.
- **Read the PDF**, not just the build log. A build that succeeds can still
  have an orphaned final page, a section split across a page break, or text
  running past a panel edge.
- **Deliver the PDF** with a `MEDIA:` path.

## Privacy

The user decides what is public. A project marked `visible: false` never
appears in a rendered CV. Before including a private project, an unreleased
product, or an employer-sensitive detail, say what you are about to include
and let the user object. When a name cannot be used, describe the work
instead of redacting the bullet into meaninglessness.

## What the raw layer is for

`raw/` is the immutable record of what the user actually said. Corrections
go in the profile, not by editing a raw file. When the profile and a raw
source disagree, the raw file stays as it was (it is a dated record) and the
profile carries the correction.

## Pitfalls

- **Do not edit the generated `.tex` to change what a CV says.** It is a view.
  Edit the profile, then rebuild. An edit to the `.tex` alone is lost on the
  next build.
- **Do not put LaTeX in a profile field.** Use the inline markup. LaTeX in a
  profile ties it to one template and breaks the escaping guarantee.
- **Do not skip the unmatched-terms report.** A tailored CV that silently
  ignores a core requirement is worse than one that says "I have no
  experience with X".
- **Do not re-derive what a tool already computed.** The match scores, the
  page count, and the overfull count are all returned by the tools; read
  them rather than recomputing or guessing.
- **Do not re-ingest a source you already have.** `career_ingest` hashes the
  content and reports `unchanged`; that is a feature, not a failure.
- **A skill the source implies but the user has not claimed is a question,
  not a fact.** Ask.
