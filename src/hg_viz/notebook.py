"""Jupyter integration: rich display of DOT source."""

from __future__ import annotations

import html as _html

from .render import has_graphviz, render_dot

__all__ = ("GraphView",)

VIZ_JS = "https://cdn.jsdelivr.net/npm/@viz-js/viz@3.11.0/lib/viz-standalone.js"


class GraphView:
    """Wraps DOT source; renders as SVG in notebooks.

    Uses the local Graphviz install when present, otherwise falls back to
    rendering in the browser with viz.js.
    """

    def __init__(self, dot: str):
        self.dot = dot

    def __str__(self) -> str:
        return self.dot

    def svg(self) -> str:
        return render_dot(self.dot, "svg").decode("utf-8")

    def _repr_mimebundle_(self, include=None, exclude=None):
        if has_graphviz():
            return {"image/svg+xml": self.svg(), "text/plain": "<hgraph graph>"}
        return {"text/html": self._browser_html(), "text/plain": "<hgraph graph>"}

    def _browser_html(self) -> str:
        import json
        import uuid

        div = f"hgviz-{uuid.uuid4().hex[:8]}"
        return (
            f'<div id="{div}">rendering…</div>'
            f'<script src="{VIZ_JS}"></script>'
            f"<script>Viz.instance().then(v => {{ const el = document.getElementById({json.dumps(div)});"
            f" el.innerHTML = ''; el.appendChild(v.renderSVGElement({json.dumps(self.dot)})); }});</script>"
        )

    def save(self, path):
        from .render import write_dot

        return write_dot(self.dot, path)

    def __repr__(self) -> str:
        return f"GraphView({len(self.dot)} chars of DOT; {_html.escape(self.dot[:40])!s}…)"
