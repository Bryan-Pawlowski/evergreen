"""Inline markup -> LaTeX.

Profile text is plain text with a small inline markup, never raw LaTeX. This
module is the only place that converts user prose into LaTeX, which is what
keeps a profile portable across templates and stops a profile from injecting
arbitrary macros.

Markup:
    **bold**          -> \\textbf{bold}
    _italic_          -> \\textit{italic}
    `code`            -> \\texttt{code}
    [label](url)      -> \\href{url}{label}

Everything else is escaped for LaTeX.
"""

from __future__ import annotations

import re

# Characters that are special to TeX.
_SPECIALS = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "^": r"\textasciicircum{}",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "%": r"\%",
}

# Typographic characters the renderer normalises to native TeX.
_TYPO = {
    "–": "--",        # en dash
    "—": "---",       # em dash
    "…": r"\ldots{}",
    " ": " ",    # nbsp
    "‘": "`",
    "’": "'",
    "“": "``",
    "”": "''",
}

# Bold, italic, code, link. The groups are, in order:
#   1: bold body   2: italic body   3: code body   4: link label   5: link url
_TOKEN_RE = re.compile(
    r"\*\*(.+?)\*\*"
    r"|(?<![\w\\])_(.+?)_(?![\w])"
    r"|`([^`]+)`"
    r"|\[([^\]]*)\]\(([^)\s]+)\)",
    re.DOTALL,
)

# Markup nesting: bold may contain italic/code/link, and vice versa. A cycle
# would mean unbounded recursion, so the depth is bounded.
_MAX_DEPTH = 6


def escape(text: str) -> str:
    """Escape plain text for LaTeX. Does not interpret markup."""
    out = []
    for ch in text:
        if ch in _SPECIALS:
            out.append(_SPECIALS[ch])
        elif ch in _TYPO:
            out.append(_TYPO[ch])
        else:
            out.append(ch)
    return "".join(out)


def _render(text: str, depth: int) -> str:
    """Convert markup to LaTeX, recursing into nested markup."""
    if depth > _MAX_DEPTH:
        return escape(text)

    out = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        out.append(escape(text[pos:m.start()]))
        pos = m.end()

        bold, italic, code, label, url = m.groups()
        if bold is not None:
            out.append(r"\textbf{" + _render(bold, depth + 1) + "}")
        elif italic is not None:
            out.append(r"\textit{" + _render(italic, depth + 1) + "}")
        elif code is not None:
            # Code contents are literal: no nested markup, just escaping.
            out.append(r"\texttt{" + escape(code) + "}")
        else:
            out.append(r"\href{" + escape(url) + "}{" + _render(label, depth + 1) + "}")

    out.append(escape(text[pos:]))
    return "".join(out)


def inline(text: str) -> str:
    """Convert plain text with inline markup into LaTeX."""
    if not text:
        return ""
    return _render(text, 0)


def plain(text: str) -> str:
    """Strip markup, leaving readable plain text (for keyword matching)."""
    if not text:
        return ""
    text = re.sub(r"\[([^\]]*)\]\(([^)\s]+)\)", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"`([^`]+)`", lambda m: m.group(1), text)
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: m.group(1), text, flags=re.DOTALL)
    text = re.sub(r"(?<![\w\\])_(.+?)_(?![\w])", lambda m: m.group(1), text, flags=re.DOTALL)
    return text


def words(text: str) -> list[str]:
    """Lowercase word tokens for matching, markup and punctuation removed."""
    cleaned = plain(text or "").lower()
    cleaned = cleaned.replace("&", " and ")
    return [w for w in re.findall(r"[a-z0-9+#][a-z0-9+#.\-/]*", cleaned) if len(w) > 1]


__all__ = ["escape", "inline", "plain", "words"]
