"""Workspace-level operations: init, ingest, and the intake checklist.

The workspace is a user's data directory. This module owns the parts that
are about *managing* it rather than rendering it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .career import CareerProfileError, Workspace

# The intake checklist: what a new workspace needs before a CV can be
# generated. The order matters: it is the order the questions get asked.
CHECKLIST: list[dict] = [
    {
        "key": "identity",
        "label": "Name and headline",
        "prompt": "What name should go on the CV, and what headline line "
                  "(the one-line role summary) fits you?",
        "slots": ["identity"],
    },
    {
        "key": "summary",
        "label": "Professional summary",
        "prompt": "In two or three sentences, what do you do and what are you "
                  "known for? This becomes the paragraph under your name.",
        "slots": ["summary"],
    },
    {
        "key": "resume",
        "label": "Existing CV or resume",
        "prompt": "Paste a recent CV, or give me a file path or a link. I will "
                  "extract it and use it to draft your entries.",
        "slots": ["experience", "education"],
    },
    {
        "key": "repos",
        "label": "GitHub account or repositories",
        "prompt": "What is your GitHub handle, or are there specific "
                  "repositories worth pulling in? Public ones I can read "
                  "directly; for private ones, point me at a local clone.",
        "slots": ["projects"],
    },
    {
        "key": "links",
        "label": "Links to your work",
        "prompt": "Any links to things you built, shipped, or wrote? Store "
                  "pages, project sites, articles, talks.",
        "slots": ["links", "projects"],
    },
    {
        "key": "experience",
        "label": "Work history",
        "prompt": "For each role: what was your title, who did you work for, "
                  "when, and what did you actually ship? The \"what did you "
                  "ship\" part matters most.",
        "slots": ["experience"],
    },
    {
        "key": "projects",
        "label": "Selected projects",
        "prompt": "Which projects are worth showing on a CV, and which should "
                  "stay private? I will mark the private ones so they never "
                  "appear in a rendered CV.",
        "slots": ["projects"],
    },
    {
        "key": "skills",
        "label": "Skills",
        "prompt": "What are your skills, grouped the way you want them shown? "
                  "I will ask about the ones I am unsure you want to claim.",
        "slots": ["skills"],
    },
    {
        "key": "education",
        "label": "Education",
        "prompt": "What did you study, where, and when? Anything worth "
                  "highlighting (honours, thesis, coursework)?",
        "slots": ["education"],
    },
]


def checklist_path(workspace: Workspace) -> Path:
    return workspace.meta_dir / "intake.json"


def load_checklist(workspace: Workspace) -> dict:
    path = checklist_path(workspace)
    if not path.exists():
        return {item["key"]: {"done": False, "answer": None} for item in CHECKLIST}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {item["key"]: {"done": False, "answer": None} for item in CHECKLIST}
    base = {item["key"]: {"done": False, "answer": None} for item in CHECKLIST}
    base.update(data or {})
    return base


def save_checklist(workspace: Workspace, state: dict) -> None:
    workspace.meta_dir.mkdir(parents=True, exist_ok=True)
    checklist_path(workspace).write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def next_question(state: dict) -> dict | None:
    """The first unanswered checklist item, or None when intake is complete."""
    for item in CHECKLIST:
        entry = state.get(item["key"]) or {}
        if not entry.get("done"):
            return item
    return None


def intake_progress(state: dict) -> dict:
    done = sum(1 for item in CHECKLIST if (state.get(item["key"]) or {}).get("done"))
    return {
        "done": done,
        "total": len(CHECKLIST),
        "complete": done == len(CHECKLIST),
        "remaining": [i["key"] for i in CHECKLIST
                      if not (state.get(i["key"]) or {}).get("done")],
    }


# --------------------------------------------------------------------------
# Raw source ingest
# --------------------------------------------------------------------------

def ingest_text(workspace: Workspace, text: str, source: str,
                kind: str = "document") -> dict:
    """Store a source document in ``raw/`` with a content hash.

    The raw layer is immutable: re-ingesting the same content is a no-op
    rather than an edit, and a changed body is stored as a new document
    rather than overwriting the old one.
    """
    workspace.raw_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (source or kind).lower()).strip("-") or kind
    today = date.today().isoformat()

    # The hash is over the CANONICAL body — the same bytes that get written
    # — so a re-ingest compares equal and `verify_raw` does not report every
    # file as drift.
    body = _canonical(text)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()

    # Find the first free path: the plain name, then -r2, -r3, ...
    path = workspace.raw_dir / f"{slug}-{today}.md"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if hashlib.sha256(_body(existing).encode("utf-8")).hexdigest() == digest:
            return {"path": str(path), "status": "unchanged", "sha256": digest}
        n = 2
        while True:
            path = workspace.raw_dir / f"{slug}-{today}-r{n}.md"
            if not path.exists():
                break
            existing = path.read_text(encoding="utf-8")
            if hashlib.sha256(_body(existing).encode("utf-8")).hexdigest() == digest:
                return {"path": str(path), "status": "unchanged",
                        "sha256": digest}
            n += 1

    front = (
        "---\n"
        f"source: {source}\n"
        f"kind: {kind}\n"
        f"ingested: {today}\n"
        f"sha256: {digest}\n"
        "---\n\n"
    )
    path.write_text(front + body, encoding="utf-8")
    return {"path": str(path), "status": "ingested", "sha256": digest}


def _canonical(text: str) -> str:
    """The body exactly as it is stored (and therefore exactly as hashed)."""
    return text.rstrip() + "\n"


def _body(text: str) -> str:
    """The content after the frontmatter block."""
    if not text.startswith("---"):
        return text
    close = text.find("\n---", 3)
    if close == -1:
        return text
    return text[close + 4:].lstrip("\n")


def verify_raw(workspace: Workspace) -> list[dict]:
    """Re-hash every raw file and report drift."""
    out: list[dict] = []
    if not workspace.raw_dir.is_dir():
        return out
    for path in sorted(workspace.raw_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^sha256:\s*([0-9a-f]{64})\s*$", text, re.MULTILINE)
        if not m:
            out.append({"path": str(path), "status": "no-hash"})
            continue
        actual = hashlib.sha256(_body(text).encode("utf-8")).hexdigest()
        out.append({
            "path": str(path),
            "status": "ok" if actual == m.group(1) else "drift",
            "expected": m.group(1),
            "actual": actual,
        })
    return out


@dataclass
class TemplateInfo:
    id: str
    name: str
    description: str
    source: str


def list_templates(workspace: Workspace | None = None,
                   shipped_root: Path | None = None) -> list[TemplateInfo]:
    """List the templates available, with the source of each."""
    from .render import discover_templates

    roots: list[tuple[Path, str]] = []
    if shipped_root:
        roots.append((Path(shipped_root) / "templates", "shipped"))
    if workspace:
        roots.append((workspace.local_templates_dir, "local"))

    infos: list[TemplateInfo] = []
    for root, source in roots:
        for t in discover_templates(root):
            infos.append(TemplateInfo(t.id, t.name, t.description, source))
    return infos


__all__ = [
    "CHECKLIST",
    "TemplateInfo",
    "checklist_path",
    "ingest_text",
    "intake_progress",
    "list_templates",
    "load_checklist",
    "next_question",
    "save_checklist",
    "verify_raw",
]
