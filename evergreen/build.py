"""Compile a rendered .tex to PDF with tectonic, and report the result.

The build is deliberately conservative, because LaTeX builds fail in ways that
are easy to misread:

- The success signal is ``note: Writing <name>.pdf`` on stdout. The exit code
  alone is not trusted.
- ``--keep-logs`` is always passed. Without it tectonic skips writing the
  ``.log`` in a no-tty run, which leaves a *stale* log from a previous
  (possibly failed) build in place, and reading that reports errors from a run
  that actually succeeded.
- ``FONTCONFIG_FILE`` is set when the template ships a ``fonts.conf``, and the
  build runs *in* the staged directory, because fontconfig's relative
  ``<dir prefix="cwd">`` resolves against the working directory.
- stdin is closed, so a binary that probes stdin does not hang the build.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zlib
from dataclasses import dataclass, field
from pathlib import Path

# Tectonic's engine selection.
#
# Tectonic 0.17 is XeTeX-based: it has no --xetex/--pdflatex/--lualatex
# flags (passing one is an "unexpected argument" error), and the default
# `--pass default` runs XeTeX + xdvipdfmx. A different TeX *format* can be
# requested with --format, but the stock bundle ships only the "latex"
# format, so the others fail with "failed to open input file
# tectonic-format-lualatex.tex".
#
# So: the template's declared engine is honoured when tectonic can serve it,
# and otherwise the build runs on tectonic's default engine with a warning.
_DEFAULT_ENGINE = "xelatex"
_FORMAT_FOR_ENGINE = {
    "xelatex": "latex",
    "lualatex": "lualatex",
    "pdflatex": "pdflatex",
}

_SUCCESS_RE = re.compile(r"note:\s*Writing\s+(?P<name>\S+\.pdf)")
_OVERFULL_RE = re.compile(r"^Overfull \\\\[hv]box", re.MULTILINE)
_UNDERFULL_RE = re.compile(r"^Underfull \\\\[hv]box", re.MULTILINE)
_UNDEFINED_RE = re.compile(r"^! (?:Undefined control sequence|LaTeX Error)", re.MULTILINE)


class BuildError(Exception):
    """Raised when the build cannot run at all (no engine, bad template)."""


@dataclass
class BuildResult:
    ok: bool
    pdf: Path | None = None
    pages: int | None = None
    overfull: int = 0
    underfull: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    log_path: Path | None = None
    stdout: str = ""
    command: list[str] = field(default_factory=list)
    engine: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "pdf": str(self.pdf) if self.pdf else None,
            "pages": self.pages,
            "overfull_boxes": self.overfull,
            "underfull_boxes": self.underfull,
            "engine": self.engine,
            "errors": self.errors,
            "warnings": self.warnings,
            "log": str(self.log_path) if self.log_path else None,
            "command": " ".join(self.command),
        }


def find_tectonic() -> str | None:
    """Locate the tectonic executable."""
    found = shutil.which("tectonic")
    if found:
        return found
    home = Path.home()
    for candidate in (home / ".local" / "bin" / "tectonic",
                      home / ".local" / "bin" / "tectonic.exe",
                      home / "bin" / "tectonic"):
        if candidate.exists():
            return str(candidate)
    return None


def tectonic_install_hint() -> str:
    return (
        "tectonic was not found. It is a single static binary with no TeX "
        "distribution to install:\n"
        "  - download the release for your platform from\n"
        "    https://github.com/tectonic-typesetting/tectonic/releases\n"
        "  - place the binary at ~/.local/bin/tectonic (or on your PATH)\n"
        "  - there is no winget package; `winget search tectonic` returning "
        "nothing is expected, not a failure.\n"
        "With tectonic installed, no LaTeX distribution is needed: the engine "
        "fetches the packages it needs on first run."
    )


def _iter_streams(data: bytes):
    """Yield the raw bytes of every ``stream ... endstream`` block."""
    for match in re.finditer(rb"stream\r?\n", data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end == -1:
            continue
        yield data[start:end]


def _decompress(blob: bytes) -> bytes | None:
    """Try to inflate a stream; return None when it is not FlateDecode."""
    for candidate in (blob, blob.rstrip(b"\r\n")):
        try:
            return zlib.decompress(candidate)
        except zlib.error:
            continue
    return None


def _count_from(blob: bytes) -> int | None:
    """Find the page-tree ``/Count`` in a blob of PDF syntax."""
    for match in re.finditer(rb"/Type\s*/Pages\b", blob):
        window = blob[match.start():match.start() + 600]
        count = re.search(rb"/Count\s+(\d+)", window)
        if count:
            return int(count.group(1))
    return None


def _page_count(pdf: Path) -> int | None:
    """Count PDF pages without a PDF library.

    Modern PDFs keep the page tree inside a compressed *object stream*, so a
    plain regex over the file finds nothing. This scans the raw file first,
    then inflates every stream and rescans, which reaches the object streams.
    """
    try:
        data = pdf.read_bytes()
    except OSError:
        return None

    found = _count_from(data)
    if found is not None:
        return found

    for blob in _iter_streams(data):
        inflated = _decompress(blob)
        if inflated is None:
            continue
        found = _count_from(inflated)
        if found is not None:
            return found

    # Last resort: count the leaf page objects.
    count = 0
    for blob in [data] + [d for d in
                          (_decompress(b) for b in _iter_streams(data))
                          if d is not None]:
        count += len(re.findall(rb"/Type\s*/Page(?![s/\w])", blob))
    return count or None


def build_tex(main_tex: Path, workdir: Path, engine: str = "lualatex",
              fontconfig: str | None = None, timeout: int = 300) -> BuildResult:
    """Compile ``main_tex`` inside ``workdir`` and return a BuildResult."""
    main_tex = Path(main_tex)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    tectonic = find_tectonic()
    if not tectonic:
        raise BuildError(tectonic_install_hint())

    flags: list[str] = []
    engine_used = engine
    if engine != _DEFAULT_ENGINE:
        # Try the requested format; fall back to tectonic's default engine
        # with a warning rather than failing the build outright.
        flags = ["--format", _FORMAT_FOR_ENGINE.get(engine, engine)]
        engine_used = engine
    cmd = [tectonic, *flags, "--keep-logs", "--keep-intermediates", main_tex.name]

    env = dict(os.environ)
    if fontconfig:
        # fontconfig's relative <dir prefix="cwd"> resolves against the CWD,
        # so the build must run in the directory holding fonts.conf.
        env["FONTCONFIG_FILE"] = str(Path(fontconfig).resolve())
    env.setdefault("TECTONIC_CACHE_DIR", str(Path.home() / ".cache" / "tectonic"))

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workdir),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return BuildResult(
            ok=False,
            errors=[f"Build timed out after {timeout}s."],
            command=cmd,
        )
    except OSError as exc:
        raise BuildError(f"Could not run tectonic: {exc}") from exc

    stdout = (proc.stdout or "") + (proc.stderr or "")
    log_path = workdir / (main_tex.stem + ".log")
    log_text = ""
    if log_path.exists():
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""

    pdf = workdir / (main_tex.stem + ".pdf")
    success = _SUCCESS_RE.search(stdout)
    pdf_ok = pdf.exists() and success is not None

    errors: list[str] = []
    warnings: list[str] = []

    # The requested engine's format was not available (the stock tectonic
    # bundle ships only the "latex"/XeTeX format). Retry on the default
    # engine rather than failing: a build on a slightly different engine is
    # far more useful than no build, and the warning says what happened.
    if not pdf_ok and flags:
        retry_cmd = [tectonic, "--keep-logs", "--keep-intermediates", main_tex.name]
        try:
            retry = subprocess.run(
                retry_cmd, cwd=str(workdir), env=env,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=timeout, errors="replace",
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            errors.append(f"Retry on the default engine failed: {exc}")
        else:
            retry_stdout = (retry.stdout or "") + (retry.stderr or "")
            retry_pdf = workdir / (main_tex.stem + ".pdf")
            if retry_pdf.exists() and _SUCCESS_RE.search(retry_stdout):
                stdout = retry_stdout
                cmd = retry_cmd
                pdf = retry_pdf
                success = _SUCCESS_RE.search(stdout)
                pdf_ok = True
                engine_used = _DEFAULT_ENGINE
                warnings.append(
                    f"tectonic could not load the {engine!r} format, so the build "
                    f"ran on its default engine ({_DEFAULT_ENGINE}). The output is "
                    f"correct for that engine; use a template that targets "
                    f"{_DEFAULT_ENGINE} if the difference matters."
                )
                if log_path.exists():
                    try:
                        log_text = log_path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        log_text = ""

    if not pdf_ok:
        for m in _UNDEFINED_RE.finditer(log_text or stdout):
            line = log_text[m.start():log_text.find("\n", m.start())]
            errors.append(line.strip())
        if not errors:
            tail = (stdout or "").strip().splitlines()[-12:]
            errors.append("Build did not produce a PDF. Last output:\n  " +
                          "\n  ".join(tail) if tail else
                          "Build did not produce a PDF and printed nothing.")

    overfull = len(_OVERFULL_RE.findall(log_text))
    underfull = len(_UNDERFULL_RE.findall(log_text))

    pages = _page_count(pdf) if pdf_ok else None

    if overfull:
        warnings.append(
            f"{overfull} overfull hbox(es). An overfull hbox means a line runs "
            f"past its column; a word past the page edge is clipped."
        )

    return BuildResult(
        ok=pdf_ok,
        pdf=pdf if pdf_ok else None,
        pages=pages,
        overfull=overfull,
        underfull=underfull,
        errors=errors,
        warnings=warnings,
        log_path=log_path if log_path.exists() else None,
        stdout=stdout,
        command=cmd,
        engine=engine_used,
    )


__all__ = ["BuildError", "BuildResult", "build_tex", "find_tectonic",
           "tectonic_install_hint"]
