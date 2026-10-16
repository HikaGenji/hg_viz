"""Capture the evolution of a running hgraph graph, one frame per engine cycle.

:class:`GraphRecorder` is an hgraph lifecycle observer.  It pairs the native
``GraphDiagnostics`` observer (which owns the measurements) with a light
Python observer that takes a snapshot after each root-graph evaluation and
works out which nodes evaluated, which outputs ticked and therefore which
edges carried data in that cycle.

Usage::

    rec = GraphRecorder()
    run_graph(my_graph, life_cycle_observers=rec.observers, ...)
    # or: eval_node(my_graph, ..., __observers__=rec.observers)

    rec.write_html("flow.html")    # interactive timeline player
    rec.write("graph.svg")         # static picture of the final state
    rec.to_dot(frame=3)            # DOT of a particular cycle
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .dot import DotOptions, format_duration, format_value, to_dot
from .model import GraphModel, VizEdge, VizNode, from_snapshot, short_type

__all__ = ("Frame", "GraphRecorder")


@dataclass(frozen=True)
class Frame:
    """The state of the graph after one (sampled) engine cycle."""

    index: int  # position in the recording (monotonic, survives trimming)
    cycle: int  # root-graph evaluation count when captured
    evaluation_time: datetime | None
    wall_clock: float  # time.time() at capture
    model: GraphModel
    evaluated: frozenset[int]  # nodes evaluated since the previous frame
    ticked: frozenset[int]  # nodes whose output was modified since the previous frame
    active_edges: frozenset[str]  # VizEdge.key of edges whose source ticked

    def to_dot(self, options: DotOptions | None = None, **overrides) -> str:
        """DOT for this frame with its data flow highlighted."""
        overrides.setdefault("active_nodes", self.evaluated | self.ticked)
        overrides.setdefault("active_edges", self.active_edges)
        return to_dot(self.model, options, **overrides)


class GraphRecorder:
    """Record snapshots of a running graph for visualisation.

    :param capture_values: capture input/output values (needed to display
        values; costs a serialisation of every port per captured cycle).
    :param every: capture one cycle in ``every``.
    :param min_interval: minimum wall-clock seconds between captures (useful
        for real-time graphs).
    :param max_frames: keep at most this many frames (oldest are dropped).
    :param max_value_chars: values are clipped to this length in viewer payloads.
    """

    def __init__(
        self,
        *,
        capture_values: bool = True,
        every: int = 1,
        min_interval: float = 0.0,
        max_frames: int | None = 2000,
        max_value_chars: int = 400,
    ):
        from hgraph.debug import GraphDiagnostics

        self.diagnostics = GraphDiagnostics(capture_values=capture_values)
        self.every = max(int(every), 1)
        self.min_interval = min_interval
        self.max_value_chars = max_value_chars
        self.frames: deque[Frame] = deque(maxlen=max_frames)
        self.final: GraphModel | None = None
        self._cycle = 0
        self._index = 0
        self._last_capture = 0.0
        self._prev: Frame | None = None
        self._lock = threading.RLock()
        self._listeners: list[Callable[[Frame], None]] = []
        # Union of everything seen -- the stable layout used by the viewer.
        self._union_nodes: dict[int, VizNode] = {}
        self._union_edges: dict[str, VizEdge] = {}
        self.topology_version = 0
        self._payloads: deque[dict] = deque(maxlen=max_frames)
        self.running = False  # True between graph start and stop

    # -- observer protocol -------------------------------------------------
    @property
    def observers(self) -> tuple:
        """Pass to ``run_graph(life_cycle_observers=...)`` /
        ``GraphConfiguration(life_cycle_observers=...)`` /
        ``eval_node(__observers__=...)``."""
        return (self.diagnostics, self)

    def on_after_graph_evaluation(self, graph) -> None:
        if graph.parent_node is not None:
            return  # nested graph: captured with its root
        self._cycle += 1
        if self._cycle % self.every:
            return
        now = time.monotonic()
        if self.min_interval and now - self._last_capture < self.min_interval:
            return
        self._last_capture = now
        try:
            et = graph.evaluation_clock.evaluation_time
        except Exception:  # pragma: no cover - defensive
            et = None
        self.capture(evaluation_time=et)

    def on_after_start_graph(self, graph) -> None:
        if graph.parent_node is None:
            self.running = True

    def on_before_stop_graph(self, graph) -> None:
        if graph.parent_node is None:
            # Last chance to see the live topology (after stop every
            # entity reports stopped=True).
            self.final = from_snapshot(self.diagnostics.snapshot())

    def on_after_stop_graph(self, graph) -> None:
        if graph.parent_node is None:
            self.running = False
        if graph.parent_node is None and self.final is not None:
            # Refresh measurements (stop counts etc.) but keep the live
            # started/stopped flags captured before the stop.
            done = from_snapshot(self.diagnostics.snapshot())
            live = self.final.nodes
            nodes = {
                i: _merge_live(n, live.get(i)) for i, n in done.nodes.items()
            }
            self.final = GraphModel(nodes, done.edges, done.evaluation_time, done.wall_time, done.graph_cycles)

    # -- capture -----------------------------------------------------------
    def add_listener(self, fn: Callable[[Frame], None]) -> None:
        """Call ``fn(frame)`` (on the engine thread) for every captured frame."""
        self._listeners.append(fn)

    def capture(self, evaluation_time: datetime | None = None) -> Frame:
        """Take a snapshot now (normally called by the observer callbacks)."""
        model = from_snapshot(self.diagnostics.snapshot())
        if evaluation_time is not None:
            model = GraphModel(model.nodes, model.edges, evaluation_time, model.wall_time, model.graph_cycles)
        with self._lock:
            prev = self._prev
            prev_time = prev.evaluation_time if prev is not None else None
            now_time = model.evaluation_time
            prev_counts = {i: n.eval_count for i, n in prev.model.nodes.items()} if prev else {}
            evaluated = frozenset(
                i for i, n in model.nodes.items()
                if not n.is_graph and n.eval_count > prev_counts.get(i, 0)
            )
            ticked = frozenset(
                i for i, n in model.nodes.items()
                if not n.is_graph
                and n.output.last_modified is not None
                and (prev_time is None or n.output.last_modified > prev_time)
                and (now_time is None or n.output.last_modified <= now_time)
            )
            # A keyed binding (output_path names a collection element) only
            # counts when its consumer evaluated: the runtime reports
            # modification for the whole collection, not per key.
            active = frozenset(
                e.key for e in model.edges
                if e.src in ticked and (not e.output_path or e.dst in evaluated)
            )
            frame = Frame(self._index, self._cycle, now_time, time.time(), model, evaluated, ticked, active)
            self._index += 1
            self.frames.append(frame)
            self._prev = frame
            self._merge_topology(model)
            self._payloads.append(self._payload(frame))
        for fn in self._listeners:
            fn(frame)
        return frame

    def _merge_topology(self, model: GraphModel) -> None:
        changed = False
        for i, n in model.nodes.items():
            if i not in self._union_nodes:
                changed = True
            self._union_nodes[i] = n
        for e in model.edges:
            if e.key not in self._union_edges:
                self._union_edges[e.key] = e
                changed = True
        if changed:
            self.topology_version += 1

    # -- access ------------------------------------------------------------
    def model(self, frame: int | None = None) -> GraphModel:
        """The model of ``frame`` (index into :attr:`frames`, negative allowed),
        or the final/latest state when ``frame`` is None."""
        with self._lock:
            if frame is not None:
                return self.frames[frame].model
            if self.final is not None:
                return self.final
            if self.frames:
                return self.frames[-1].model
        return from_snapshot(self.diagnostics.snapshot())

    def union_model(self) -> GraphModel:
        """Every node and edge seen during the recording (the viewer layout)."""
        with self._lock:
            if not self._union_nodes:
                return self.model()
            nodes = dict(self._union_nodes)
            if self.final is not None:
                nodes.update(self.final.nodes)
            # children lists must cover instances that came and went
            kids: dict[int, list[int]] = {}
            for n in nodes.values():
                kids.setdefault(n.parent_id, []).append(n.id)
            from dataclasses import replace

            nodes = {i: replace(n, children=tuple(sorted(kids.get(i, ())))) for i, n in nodes.items()}
            edges = dict(self._union_edges)
            if self.final is not None:
                for e in self.final.edges:
                    edges.setdefault(e.key, e)
            last = self.frames[-1].evaluation_time if self.frames else None
            return GraphModel(nodes, tuple(edges.values()), last)

    def to_dot(self, frame: int | None = None, options: DotOptions | None = None, **overrides) -> str:
        """DOT of a frame (with its data flow highlighted) or of the final state."""
        if frame is not None:
            return self.frames[frame].to_dot(options, **overrides)
        return to_dot(self.model(), options, **overrides)

    def write(self, path, frame: int | None = None, options: DotOptions | None = None, **overrides):
        """Write the graph to ``path`` (``.svg``, ``.png``, ``.pdf``, ``.dot`` ...)."""
        from .render import write_dot

        return write_dot(self.to_dot(frame, options, **overrides), path)

    def write_html(self, path, *, title: str | None = None, options: DotOptions | None = None):
        """Write a self-contained interactive timeline viewer to ``path``."""
        from .html import write_html

        return write_html(self, path, title=title, options=options)

    def view(self, frame: int | None = None, **overrides):
        """A notebook-displayable view (SVG) of a frame or the final state."""
        from .notebook import GraphView

        return GraphView(self.to_dot(frame, **overrides))

    def summary(self, top: int = 10) -> str:
        """A text table of the busiest nodes."""
        m = self.model()
        nodes = sorted((n for n in m.nodes.values() if not n.is_graph), key=lambda n: -n.eval_time)
        lines = [f"{'node':40} {'evals':>7} {'time':>9}  output"]
        for n in nodes[:top]:
            out = format_value(n.output.value, 30) if n.output.valid else ""
            lines.append(f"{_clip_name(n):40} {n.eval_count:>7} {format_duration(n.eval_time):>9}  {out}")
        return "\n".join(lines)

    # -- viewer payloads ---------------------------------------------------
    def payloads(self, after: int = -1, limit: int | None = None) -> list[dict]:
        """Compact JSON-able frame descriptions for the HTML viewer."""
        with self._lock:
            out = [p for p in self._payloads if p["i"] > after]
        return out[:limit] if limit else out

    def _payload(self, frame: Frame) -> dict:
        mx = self.max_value_chars
        nodes: dict[str, Any] = {}
        for i, n in frame.model.nodes.items():
            if n.is_graph:
                continue
            d: dict[str, Any] = {
                "c": n.eval_count,
                "us": round(n.eval_time.total_seconds() * 1e6, 1),
            }
            if n.stopped:
                d["dead"] = 1
            if i in frame.evaluated:
                d["ev"] = 1
            if i in frame.ticked:
                d["tk"] = 1
            if n.output.schema:
                d["v"] = format_value(n.output.value, 28) if n.output.valid else "∅"
                d["o"] = _clip(n.output.json, mx) if n.output.valid else None
                d["ot"] = short_type(n.output.schema)
            if n.input.json is not None:
                d["in"] = {k: _clip(_json(v), mx) for k, v in n.input_fields().items()}
            nodes[str(i)] = d
        return {
            "i": frame.index,
            "cycle": frame.cycle,
            "t": frame.evaluation_time.isoformat(sep=" ") if frame.evaluation_time else None,
            "wall": frame.wall_clock,
            "nodes": nodes,
            "edges": sorted(frame.active_edges),
        }


def _merge_live(done: VizNode, live: VizNode | None) -> VizNode:
    from dataclasses import replace

    if live is None:
        return done
    return replace(done, label=live.label, started=live.started, stopped=live.stopped, input=live.input,
                   output=live.output)


def _json(v: Any) -> str:
    import json

    try:
        return json.dumps(v, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(v)


def _clip(text: str | None, width: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= width else text[: width - 1] + "…"


def _clip_name(n: VizNode) -> str:
    name = n.path or n.label
    return name if len(name) <= 40 else "…" + name[-39:]
