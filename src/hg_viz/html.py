"""Self-contained HTML timeline viewer for a :class:`~hg_viz.GraphRecorder`.

The page lays the graph out once (the union of every node/edge seen while
recording, so the picture is stable while stepping) and then replays the
recorded frames on top of it: nodes that evaluated light up, edges whose
source ticked animate, values and evaluation counts update in place, nested
graph instances that do not exist yet / any more fade out.  Clicking a node
shows its inputs (and where they come from), output and statistics.

When Graphviz is installed locally the layout is embedded as SVG; otherwise
the DOT is rendered in the browser with viz.js (loaded from a CDN).
"""

from __future__ import annotations

import json
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from .dot import DotOptions, to_dot
from .model import GraphModel
from .render import has_graphviz, render_dot

if TYPE_CHECKING:  # pragma: no cover
    from .recorder import GraphRecorder

__all__ = ("viewer_html", "write_html", "topology_payload", "VIEWER_OPTIONS")

VIEWER_OPTIONS = DotOptions(emit_ids=True, hide_stopped=False, max_instances=6, show_stats=True)


def _template() -> str:
    return resources.files(__package__).joinpath("viewer.html").read_text(encoding="utf-8")


def topology_payload(recorder: "GraphRecorder", options: DotOptions | None = None, *, svg: bool | None = None) -> dict:
    """Layout (DOT, optionally pre-rendered SVG) plus node/edge metadata."""
    opts = replace(options or VIEWER_OPTIONS, emit_ids=True, hide_stopped=False, active_nodes=frozenset(),
                   active_edges=frozenset(), heat=None)
    model = recorder.union_model()
    # Draw everything as alive; the per-frame state decides what is faded.
    drawn = GraphModel(
        {i: replace(n, stopped=False, started=True) for i, n in model.nodes.items()},
        model.edges,
        None,
    )
    if opts.title is None:
        roots = drawn.roots
        opts = replace(opts, title=roots[0].label if roots else "hgraph")
    dot = to_dot(drawn, opts)
    out = {
        "version": recorder.topology_version,
        "title": opts.title,
        "dot": dot,
        "svg": None,
        "nodes": {
            str(n.id): {
                "label": n.label,
                "kind": n.kind,
                "path": n.path,
                "impl": n.implementation,
                "parent": n.parent_id,
                "schema": {"in": n.input.schema, "out": n.output.schema},
            }
            for n in model.nodes.values()
        },
        "edges": {
            e.key: {"src": e.src, "dst": e.dst, "label": e.label, "input": e.input_name, "out": list(e.output_path)}
            for e in model.edges
        },
    }
    if svg if svg is not None else has_graphviz():
        try:
            out["svg"] = render_dot(dot, "svg").decode("utf-8")
        except RuntimeError:
            out["svg"] = None
    return out


def viewer_html(recorder: "GraphRecorder", *, title: str | None = None, options: DotOptions | None = None,
                live: bool = False, poll_ms: int = 1000) -> str:
    """The viewer page as a string (static unless ``live``)."""
    config: dict = {"mode": "live" if live else "static", "title": title or "hgraph", "pollMs": poll_ms}
    if not live:
        topo = topology_payload(recorder, options)
        config["topology"] = topo
        config["frames"] = recorder.payloads()
        if title is None:
            roots = recorder.union_model().roots
            config["title"] = roots[0].label if roots else "hgraph"
    blob = json.dumps(config, separators=(",", ":"), default=str).replace("</", "<\\/")
    return _template().replace("/*__HGVIZ_CONFIG__*/null", blob).replace("__HGVIZ_TITLE__", _esc(config["title"]))


def write_html(recorder: "GraphRecorder", path, *, title: str | None = None, options: DotOptions | None = None) -> Path:
    path = Path(path)
    path.write_text(viewer_html(recorder, title=title, options=options), encoding="utf-8")
    return path


def _esc(text: str) -> str:
    import html

    return html.escape(text)
