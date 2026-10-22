"""Match a job description against a profile, deterministically.

The matcher does the *selection*: which entries of the profile are relevant to
a job, and how strongly. It does not write prose. Rewording happens after, in
a separate step, and only rewrites existing bullets.

The scoring is intentionally simple and inspectable:

- Each profile entry is reduced to a bag of terms (its title, org, tags, and
  the text of its bullets).
- The job description is reduced to a bag of terms.
- An entry's score is the share of the JD's *significant* terms it covers,
  weighted by how important the term looks in the JD.
- Terms that appear in the JD only as a job title or a company name are
  treated as low weight, so "Senior Engineer" at "Acme" does not push every
  engineering entry up.

The output is a per-slot ranking with the matched and unmatched terms
attached, so a human (or the LLM polishing the wording) can see *why* an
entry was chosen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import markup
from .profile import Profile

# Terms that describe the posting rather than the work. Matching on these
# says little about which experience is relevant.
_LOW_VALUE = {
    "job", "jobs", "role", "roles", "position", "company", "team", "teams",
    "work", "working", "candidate", "candidates", "apply", "application",
    "experience", "years", "year", "salary", "benefits", "location", "remote",
    "hybrid", "onsite", "office", "employment", "fulltime", "parttime",
    "contract", "permanent", "equal", "employer", "responsibilities",
    "requirements", "qualifications", "skills", "about", "what", "you", "your",
    "we", "our", "us", "will", "are", "have", "has", "with", "for", "and",
    "the", "a", "an", "of", "to", "in", "on", "or", "at", "as", "by", "new",
    "level", "senior", "junior", "mid", "lead", "principal", "staff",
    "engineer", "engineering", "developer", "development", "manager",
    "scientist", "specialist", "professional", "position",
    # Filler that appears in every posting and matches nothing specific.
    "hiring", "hire", "own", "owning", "strong", "build", "building",
    "excellent", "good", "great", "plus", "must", "should", "able",
    "ability", "knowledge", "understanding", "familiar", "comfortable",
    "someone", "something", "anything", "everything", "including", "etc",
    "such", "other", "others", "well", "able", "help", "helps", "support",
    "supports", "like", "want", "wants", "need", "needs", "looking",
    "opportunity", "challenging", "dynamic", "fast-paced", "passionate",
    "self-starter", "teamwork", "communication", "written", "verbal",
}

# A term repeated in the JD is more likely to be a real requirement.
_MAX_WEIGHT = 3.0


@dataclass
class Matched:
    """One profile entry scored against a job description."""

    slot: str
    entry_id: str
    title: str
    score: float
    matched_terms: list[str] = field(default_factory=list)
    entry: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "id": self.entry_id,
            "title": self.title,
            "score": round(self.score, 4),
            "matched_terms": self.matched_terms,
            # The entry itself is carried so a saved report can be used to
            # render a tailored CV without re-reading the profile.
            "entry": self.entry,
        }


@dataclass
class MatchReport:
    """The full result of matching a job description against a profile."""

    jd_terms: dict[str, float] = field(default_factory=dict)
    required_terms: list[str] = field(default_factory=list)
    unmatched_terms: list[str] = field(default_factory=list)
    matched: list[Matched] = field(default_factory=list)
    by_slot: dict[str, list[Matched]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "jd_terms": {k: round(v, 3) for k, v in sorted(
                self.jd_terms.items(), key=lambda kv: -kv[1])},
            "required_terms": self.required_terms,
            "unmatched_terms": self.unmatched_terms,
            "matched": [m.to_dict() for m in self.matched],
            "by_slot": {k: [m.to_dict() for m in v] for k, v in self.by_slot.items()},
        }

    def selection(self, limit_per_slot: int = 0) -> dict[str, list[dict]]:
        """Turn the ranking into a slot -> entries selection for the renderer."""
        out: dict[str, list[dict]] = {}
        for slot, items in self.by_slot.items():
            chosen = items[:limit_per_slot] if limit_per_slot else items
            out[slot] = [m.entry for m in chosen]
        return out


def _normalise(text: str) -> list[str]:
    return markup.words(text or "")


def _clean_token(word: str) -> str:
    """Strip trailing punctuation from a token.

    A sentence-final period turns "pipeline." into a term that can never
    match the profile's "pipeline", so the trailing dot is dropped. A dot
    *inside* a token ("node.js", "c++") is meaningful and stays.
    """
    return word.rstrip(".,;:")


def jd_terms(jd_text: str) -> dict[str, float]:
    """Extract weighted terms from a job description.

    Weight = min(occurrences, _MAX_WEIGHT) / _MAX_WEIGHT, with a floor so a
    term seen once still counts for something.
    """
    counts: dict[str, int] = {}
    for word in _normalise(jd_text):
        word = _clean_token(word)
        if not word:
            continue
        counts[word] = counts.get(word, 0) + 1

    weights: dict[str, float] = {}
    for word, count in counts.items():
        if word in _LOW_VALUE or len(word) < 3:
            continue
        # A term that looks like a skill (has a +, #, /, . or is CamelCase in
        # the source) is worth more than a common English word.
        base = min(count, _MAX_WEIGHT) / _MAX_WEIGHT
        if re.search(r"[+#/.]", word):
            base = min(base + 0.5, 1.0)
        weights[word] = max(base, 0.34)
    return weights


def _entry_terms(entry: dict) -> set[str]:
    """Every term an entry covers."""
    terms: set[str] = set()

    for key in ("role", "title", "degree", "subtitle", "org", "detail", "summary"):
        value = entry.get(key)
        if isinstance(value, str):
            terms.update(_clean_token(w) for w in _normalise(value))

    for tag in entry.get("tags") or []:
        terms.update(_clean_token(w) for w in _normalise(tag))

    for bullet in entry.get("bullets") or []:
        text = bullet.get("text") if isinstance(bullet, dict) else bullet
        if isinstance(text, str):
            terms.update(_clean_token(w) for w in _normalise(text))

    return {t for t in terms if t}


def _entry_title(entry: dict) -> str:
    for key in ("role", "title", "degree", "label"):
        value = entry.get(key)
        if value:
            return str(value)
    return entry.get("id", "")


def match(profile: Profile, jd_text: str,
          slots: list[str] | None = None) -> MatchReport:
    """Rank a profile's entries against a job description."""
    terms = jd_terms(jd_text)
    report = MatchReport(jd_terms=terms)

    if not terms:
        report.required_terms = []
        return report

    # The terms a strong candidate must cover: the highest-weighted ones.
    ordered = sorted(terms.items(), key=lambda kv: -kv[1])
    report.required_terms = [t for t, _ in ordered[:18]]

    slots = slots or ["experience", "projects", "education", "skills", "milestones"]

    total_weight = sum(terms.values())
    covered: set[str] = set()

    for slot in slots:
        entries = profile.visible(slot)
        scored: list[Matched] = []
        for entry in entries:
            entry_terms = _entry_terms(entry)
            hits = {t: w for t, w in terms.items() if t in entry_terms}
            if not hits:
                continue
            # Share of the JD's weight this entry covers, plus a small bonus
            # for the user's own declared priority.
            weight = sum(hits.values())
            priority = float(entry.get("priority") or 0) / 100.0
            score = (weight / total_weight) * (1.0 + priority)
            scored.append(Matched(
                slot=slot,
                entry_id=str(entry.get("id") or entry.get("title") or ""),
                title=_entry_title(entry),
                score=score,
                matched_terms=sorted(hits, key=lambda t: -hits[t]),
                entry=entry,
            ))
            covered.update(hits)

        scored.sort(key=lambda m: -m.score)
        report.by_slot[slot] = scored
        report.matched.extend(scored)

    report.unmatched_terms = [t for t in report.required_terms if t not in covered]
    return report


def evidence_for(entry: dict, terms: list[str]) -> list[str]:
    """The bullet texts of an entry that mention any of the given terms.

    This is what a rewriter is allowed to touch: the wording of bullets that
    already speak to the job. A bullet that matches nothing is not a
    candidate for rewording, and a rewriter must not invent a new one.
    """
    wanted = set(terms)
    out: list[str] = []
    for bullet in entry.get("bullets") or []:
        text = bullet.get("text") if isinstance(bullet, dict) else bullet
        if not isinstance(text, str):
            continue
        if wanted & set(_normalise(text)):
            out.append(text)
    return out


__all__ = ["MatchReport", "Matched", "evidence_for", "jd_terms", "match"]
