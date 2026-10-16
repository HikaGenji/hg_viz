"""Turn DOT source into images using the Graphviz ``dot`` executable."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

__all__ = ("has_graphviz", "render_dot", "write_dot")


def _dot_binary() -> str | None:
    return os.environ.get("HG_VIZ_DOT") or shutil.which("dot")


def has_graphviz() -> bool:
    """``True`` when the Graphviz ``dot`` executable is available."""
    return _dot_binary() is not None


def render_dot(source: str, format: str = "svg", engine: str = "dot", timeout: float = 60.0) -> bytes:
    """Lay out ``source`` with Graphviz and return the rendered bytes.

    :raises RuntimeError: if Graphviz is not installed or fails.
    """
    binary = _dot_binary()
    if binary is None:
        raise RuntimeError(
            "Graphviz 'dot' executable not found; install Graphviz "
            "(https://graphviz.org/download/) or set HG_VIZ_DOT, or use "
            "the HTML viewer which renders in the browser."
        )
    proc = subprocess.run(
        [binary, f"-K{engine}", f"-T{format}"],
        input=source.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"graphviz failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def write_dot(source: str, path: str | os.PathLike, format: str | None = None) -> Path:
    """Write ``source`` to ``path``; the format is taken from the suffix
    (``.dot``/``.gv`` writes the DOT text itself)."""
    path = Path(path)
    fmt = format or path.suffix.lstrip(".").lower() or "svg"
    if fmt in ("dot", "gv"):
        path.write_text(source, encoding="utf-8")
    else:
        path.write_bytes(render_dot(source, fmt))
    return path
