"""hg_viz -- Graphviz visualisation and data-flow introspection for hgraph.

Quick start::

    from hg_viz import GraphRecorder

    rec = GraphRecorder()
    run_graph(my_graph, life_cycle_observers=rec.observers, ...)
    rec.write("graph.svg")         # final topology + values + eval stats
    rec.write_html("flow.html")    # step through every engine cycle

See :func:`record`, :func:`serve` and :func:`to_dot` for more.
"""

from .api import record
from .dot import DotOptions, Palette, to_dot
from .model import GraphModel, NodeKind, PortValue, VizEdge, VizNode, from_snapshot
from .recorder import Frame, GraphRecorder
from .render import has_graphviz, render_dot, write_dot
from .server import VizServer, serve

__all__ = (
    "DotOptions",
    "Frame",
    "GraphModel",
    "GraphRecorder",
    "NodeKind",
    "Palette",
    "PortValue",
    "VizEdge",
    "VizNode",
    "from_snapshot",
    "has_graphviz",
    "record",
    "render_dot",
    "serve",
    "VizServer",
    "to_dot",
    "write_dot",
)
