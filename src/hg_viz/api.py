"""One-call helpers: run a graph under a recorder."""

from __future__ import annotations

from typing import Any, Callable

from .recorder import GraphRecorder

__all__ = ("record",)


def record(fn: Callable, *args: Any, recorder: GraphRecorder | None = None, node: bool | None = None,
           **kwargs: Any) -> tuple[GraphRecorder, Any]:
    """Run ``fn`` with a :class:`GraphRecorder` attached and return ``(recorder, result)``.

    With ``node=True`` (the default when positional ``args`` are given) the
    graph is run with ``hgraph.eval_node`` -- positional args are the input
    tick sequences -- otherwise with ``hgraph.run_graph`` (``kwargs`` such as
    ``start_time``/``end_time``/``run_mode`` are passed through).

    ::

        rec, out = record(my_graph, [1, 2, 3], [10, None, 30])
        rec.write_html("my_graph.html")
    """
    import hgraph

    rec = recorder or GraphRecorder()
    if node is None:
        node = bool(args)
    if node:
        observers = tuple(kwargs.pop("__observers__", ()) or ()) + rec.observers
        result = hgraph.eval_node(fn, *args, __observers__=observers, **kwargs)
    else:
        observers = tuple(kwargs.pop("life_cycle_observers", ()) or ()) + rec.observers
        result = hgraph.run_graph(fn, *args, life_cycle_observers=observers, **kwargs)
    return rec, result
