"""A plain-python model of an hgraph runtime graph.

hgraph's native runtime exposes ``GraphDiagnostics``: a lifecycle observer
whose snapshots list every graph and node (including nested graphs created by
``map_``, ``reduce``, ``switch_`` ...) together with the *resolved* input
bindings of each node.  This module turns such a snapshot into an immutable
:class:`GraphModel` made of :class:`VizNode` / :class:`VizEdge` records that the
renderers (DOT, HTML) consume.

Nothing here depends on graphviz; the model is also handy for programmatic
introspection (``model.upstream(node)``, ``model.edges_into(node)`` ...).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

__all__ = (
    "NodeKind",
    "PortValue",
    "VizNode",
    "VizEdge",
    "GraphModel",
    "from_snapshot",
    "display_label",
    "short_type",
)


class NodeKind:
    """String constants used for :attr:`VizNode.kind`."""

    GRAPH = "graph"  # a (nested) graph instance -- rendered as a cluster
    PULL_SOURCE = "pull_source"
    PUSH_SOURCE = "push_source"
    COMPUTE = "compute"
    SINK = "sink"
    NESTED = "nested"  # a node owning nested graphs (map_, reduce, switch_ ...)


_NATIVE_KIND = {
    "PULL_SOURCE": NodeKind.PULL_SOURCE,
    "PUSH_SOURCE": NodeKind.PUSH_SOURCE,
    "COMPUTE": NodeKind.COMPUTE,
    "SINK": NodeKind.SINK,
    "NESTED": NodeKind.NESTED,
}


@dataclass(frozen=True)
class PortValue:
    """The captured state of an input, output or scalar bundle."""

    schema: str = ""
    json: str | None = None
    valid: bool = False
    last_modified: datetime | None = None

    @property
    def value(self) -> Any:
        """The decoded JSON value (``None`` when not captured)."""
        if self.json is None:
            return None
        try:
            return json.loads(self.json)
        except (TypeError, ValueError):
            return self.json


@dataclass(frozen=True)
class VizNode:
    """A graph or node entity of the runtime graph."""

    id: int
    parent_id: int  # 0 for the root graph
    kind: str
    label: str  # human friendly label (see display_label)
    raw_label: str  # label as reported by the runtime
    implementation: str
    path: str
    node_index: int
    started: bool
    stopped: bool
    eval_count: int
    eval_time: timedelta
    eval_failures: int
    children: tuple[int, ...] = ()
    input: PortValue = field(default_factory=PortValue)
    output: PortValue = field(default_factory=PortValue)
    scalars: PortValue = field(default_factory=PortValue)

    @property
    def is_graph(self) -> bool:
        return self.kind == NodeKind.GRAPH

    @property
    def alive(self) -> bool:
        """``False`` once the entity has been stopped (e.g. a removed map_ key)."""
        return not self.stopped

    def input_fields(self) -> dict[str, Any]:
        """The input bundle decoded to ``{field_name: value}``."""
        return _bundle_fields(self.input.schema, self.input.value)


@dataclass(frozen=True)
class VizEdge:
    """A resolved binding: ``src`` output (at ``output_path``) feeds ``dst`` input
    (at ``input_path``)."""

    src: int
    dst: int
    input_path: tuple[str, ...]
    output_path: tuple[str, ...] = ()
    fan: int = 1  # number of keyed bindings collapsed into this edge

    @property
    def key(self) -> str:
        """Stable identifier, used for ids in SVG / HTML output."""
        ip = ".".join(self.input_path)
        op = ".".join(self.output_path)
        return f"{self.src}>{self.dst}:{op}>{ip}"

    @property
    def input_name(self) -> str:
        return self.input_path[0] if self.input_path else ""

    @property
    def label(self) -> str:
        """``out_path → input`` style label (``qty → rhs``)."""
        name = ".".join(self.input_path) if self.fan == 1 else self.input_name
        src = ".".join(self.output_path) if self.fan == 1 else ""
        text = f"{src} → {name}" if src else name
        if self.fan > 1:
            text += f" [×{self.fan}]"
        return text


@dataclass(frozen=True)
class GraphModel:
    """Nodes and data-flow edges of a runtime graph at one point in time."""

    nodes: dict[int, VizNode]
    edges: tuple[VizEdge, ...]
    evaluation_time: datetime | None = None
    wall_time: timedelta | None = None
    graph_cycles: int = 0

    # -- navigation -------------------------------------------------------
    @property
    def roots(self) -> list[VizNode]:
        return [n for n in self.nodes.values() if n.parent_id not in self.nodes]

    def children(self, node_id: int) -> list[VizNode]:
        node = self.nodes[node_id]
        return [self.nodes[c] for c in node.children if c in self.nodes]

    def ancestors(self, node_id: int) -> list[int]:
        """Ids of the enclosing entities, innermost first."""
        out = []
        cur = self.nodes.get(node_id)
        while cur is not None and cur.parent_id in self.nodes:
            out.append(cur.parent_id)
            cur = self.nodes[cur.parent_id]
        return out

    def edges_into(self, node_id: int) -> list[VizEdge]:
        return [e for e in self.edges if e.dst == node_id]

    def edges_out_of(self, node_id: int) -> list[VizEdge]:
        return [e for e in self.edges if e.src == node_id]

    def upstream(self, node_id: int) -> list[VizNode]:
        return [self.nodes[e.src] for e in self.edges_into(node_id) if e.src in self.nodes]

    def downstream(self, node_id: int) -> list[VizNode]:
        return [self.nodes[e.dst] for e in self.edges_out_of(node_id) if e.dst in self.nodes]

    def find(self, label: str) -> list[VizNode]:
        """Nodes whose display or raw label matches ``label``."""
        return [n for n in self.nodes.values() if label in (n.label, n.raw_label)]

    def edge_value(self, edge: VizEdge) -> Any:
        """The value carried by ``edge`` as last seen by its consumer."""
        dst = self.nodes.get(edge.dst)
        if dst is None:
            return None
        value: Any = dst.input_fields()
        for part in edge.input_path:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                return None
        return value

    def filtered(self, keep: Iterable[int]) -> "GraphModel":
        """A model restricted to ``keep`` (edges between kept nodes only)."""
        keep = set(keep)
        nodes = {}
        for i in keep:
            n = self.nodes.get(i)
            if n is not None:
                nodes[i] = _replace(n, children=tuple(c for c in n.children if c in keep))
        edges = tuple(e for e in self.edges if e.src in nodes and e.dst in nodes)
        return GraphModel(nodes, edges, self.evaluation_time, self.wall_time, self.graph_cycles)

    def alive(self) -> "GraphModel":
        """Drop stopped entities (torn-down nested graphs), unless the whole
        graph has stopped, in which case the model is returned unchanged."""
        if all(n.stopped for n in self.roots):
            return self
        dead = {i for i, n in self.nodes.items() if n.stopped}
        # Anything inside a stopped graph is dead as well.
        for i in list(self.nodes):
            if any(a in dead for a in self.ancestors(i)):
                dead.add(i)
        return self.filtered(set(self.nodes) - dead)

    def to_dict(self) -> dict:
        """A JSON-serialisable representation of the model."""
        return {
            "evaluation_time": _iso(self.evaluation_time),
            "graph_cycles": self.graph_cycles,
            "nodes": [
                {
                    "id": n.id,
                    "parent_id": n.parent_id,
                    "kind": n.kind,
                    "label": n.label,
                    "raw_label": n.raw_label,
                    "implementation": n.implementation,
                    "path": n.path,
                    "started": n.started,
                    "stopped": n.stopped,
                    "eval_count": n.eval_count,
                    "eval_time_us": n.eval_time / timedelta(microseconds=1),
                    "input": _port_dict(n.input),
                    "output": _port_dict(n.output),
                    "scalars": _port_dict(n.scalars),
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {
                    "src": e.src,
                    "dst": e.dst,
                    "input_path": list(e.input_path),
                    "output_path": list(e.output_path),
                    "fan": e.fan,
                }
                for e in self.edges
            ],
        }


# ---------------------------------------------------------------------------
# Snapshot conversion
# ---------------------------------------------------------------------------


def from_snapshot(snapshot, *, collapse_keyed: bool = True) -> GraphModel:
    """Build a :class:`GraphModel` from a ``hgraph.debug.GraphDiagnosticsSnapshot``.

    :param collapse_keyed: a binding of a whole collection (``TSD``) is
        reported both for the collection and once per key; when true the
        per-key bindings are folded into the collection edge (``fan`` counts
        them).
    """
    nodes: dict[int, VizNode] = {}
    raw_edges: list[VizEdge] = []
    latest: datetime | None = None
    for entry in snapshot.entries:
        is_graph = entry.kind.name == "GRAPH"
        kind = NodeKind.GRAPH if is_graph else _NATIVE_KIND.get(entry.node_kind.name, NodeKind.COMPUTE)
        inp = _port(entry.input)
        out = _port(entry.output)
        scalars = _port(entry.scalars)
        for p in (inp, out):
            if p.last_modified is not None and (latest is None or p.last_modified > latest):
                latest = p.last_modified
        node = VizNode(
            id=entry.id,
            parent_id=entry.parent_id,
            kind=kind,
            label="",
            raw_label=entry.label,
            implementation=entry.implementation_label,
            path=entry.path,
            node_index=entry.node_index,
            started=entry.started,
            stopped=entry.stopped,
            eval_count=entry.evaluation.count,
            eval_time=entry.evaluation.total_time,
            eval_failures=entry.evaluation.failures,
            children=tuple(entry.children),
            input=inp,
            output=out,
            scalars=scalars,
        )
        nodes[entry.id] = _replace(node, label=display_label(node))
        if entry.input.available:
            for t in entry.input.targets:
                raw_edges.append(
                    VizEdge(
                        src=t.node_id,
                        dst=entry.id,
                        input_path=tuple(_decode_path(t.source_path)),
                        output_path=tuple(_decode_path(t.target_path)),
                    )
                )
    edges = _collapse(raw_edges) if collapse_keyed else tuple(_dedupe(raw_edges))
    nodes = _label_instances(nodes, edges)
    return GraphModel(
        nodes=nodes,
        edges=edges,
        evaluation_time=latest,
        wall_time=getattr(snapshot, "wall_time", None),
        graph_cycles=getattr(snapshot, "graph_cycles", 0),
    )


def _port(value) -> PortValue:
    if value is None or not value.available:
        return PortValue()
    lm = value.last_modified
    return PortValue(
        schema=value.schema_label,
        json=value.json if not value.error else None,
        valid=value.valid,
        # The runtime reports "never modified" as the epoch (MIN_DT).
        last_modified=lm if lm is not None and lm > _NEVER else None,
    )


def _decode_path(parts) -> list[str]:
    out = []
    for p in parts:
        try:
            v = json.loads(p)
        except (TypeError, ValueError):
            v = p
        out.append(str(v))
    return out


def _dedupe(edges: Iterable[VizEdge]) -> list[VizEdge]:
    seen, out = set(), []
    for e in edges:
        if e.key not in seen:
            seen.add(e.key)
            out.append(e)
    return out


def _collapse(edges: list[VizEdge]) -> tuple[VizEdge, ...]:
    """Fold keyed bindings (input path ``(field, key)``) into the binding of the
    whole collection (input path ``(field,)``) from the same source."""
    edges = _dedupe(edges)
    parents = {(e.src, e.dst, e.input_path): e for e in edges}
    fan: dict[tuple, int] = {}
    out = []
    for e in edges:
        folded = False
        for n in range(len(e.input_path) - 1, 0, -1):
            prefix = (e.src, e.dst, e.input_path[:n])
            if prefix in parents:
                fan[prefix] = fan.get(prefix, 0) + 1
                folded = True
                break
        if not folded:
            out.append(e)
    return tuple(
        _replace(e, fan=fan[(e.src, e.dst, e.input_path)]) if (e.src, e.dst, e.input_path) in fan else e
        for e in out
    )


_NEVER = datetime(1970, 1, 1)
_MISSING = object()

_HARNESS = {"__harness_replay": "replay", "__harness_record": "record"}


def display_label(node: VizNode) -> str:
    """A compact, human friendly label for a runtime entity.

    ``__py_compute:g.double`` → ``double``; ``map_:g.map_`` → ``map_``;
    ``__harness_replay`` (key ``eval_node::a``) → ``replay a``.
    """
    raw = node.raw_label or node.implementation or f"#{node.id}"
    if raw in _HARNESS:
        key = ""
        scalars = node.scalars.value
        if isinstance(scalars, list) and scalars and isinstance(scalars[0], str):
            key = scalars[0].split("::")[-1]
        return f"{_HARNESS[raw]} {key}".strip()
    if ":" in raw:
        raw = raw.split(":", 1)[1]
        raw = raw.rsplit(".", 1)[-1]
    return raw


def _label_instances(nodes: dict[int, VizNode], edges: tuple[VizEdge, ...]) -> dict[int, VizNode]:
    """Label nested graph instances with the collection key(s) they serve.

    A ``map_`` instance's inner nodes bind straight to the outer collection at
    ``output_path == (key,)``, so the key can be recovered from the edges that
    enter the instance from outside it.
    """
    owner_of: dict[int, int] = {}

    def instance_of(node_id: int) -> int | None:
        if node_id in owner_of:
            return owner_of[node_id]
        cur = nodes.get(node_id)
        res = None
        while cur is not None:
            parent = nodes.get(cur.parent_id)
            if cur.kind == NodeKind.GRAPH and parent is not None:
                res = cur.id
                break
            cur = parent
        owner_of[node_id] = res
        return res

    keys: dict[int, list[str]] = {}
    for e in edges:
        inst = instance_of(e.dst)
        if inst is None or not e.output_path:
            continue
        if instance_of(e.src) == inst:
            continue  # internal edge
        k = e.output_path[-1]
        lst = keys.setdefault(inst, [])
        if k not in lst:
            lst.append(k)
    out = dict(nodes)
    for inst, ks in keys.items():
        n = out[inst]
        out[inst] = _replace(n, label=", ".join(ks))
    for i, n in out.items():
        if n.kind == NodeKind.GRAPH and n.parent_id in out and n.label in ("graph", n.raw_label):
            label = f"#{n.id}"
            owner = out[n.parent_id]
            # switch_-style nodes select their (single live) instance by a key input.
            key = owner.input_fields().get("key", _MISSING)
            if key is not _MISSING and not n.stopped:
                label = f"key = {json.dumps(key, default=str)}"
            out[i] = _replace(n, label=label)
    return out


# ---------------------------------------------------------------------------
# Schema label helpers
# ---------------------------------------------------------------------------

_BUNDLE_RE = re.compile(r"^\s*(?:Bundle|TSB)\s*\{(.*)\}\s*$", re.S)


def bundle_field_types(schema: str) -> dict[str, str]:
    """``Bundle{lhs:int,rhs:int}`` → ``{'lhs': 'int', 'rhs': 'int'}``."""
    m = _BUNDLE_RE.match(schema or "")
    if not m:
        return {}
    out: dict[str, str] = {}
    for part in _split_top(m.group(1)):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _split_top(text: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch in "[{(<":
            depth += 1
        elif ch in "]})>":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return [p for p in parts if p.strip()]


def short_type(schema: str) -> str:
    """Shorten runtime schema labels: ``python::tsb::mod::Price`` → ``Price``."""
    if not schema:
        return ""
    return re.sub(r"python::\w+::(?:[\w.]+::)*(\w+)", r"\1", schema)


def _bundle_fields(schema: str, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    names = list(bundle_field_types(schema))
    if isinstance(value, list) and names and len(names) == len(value):
        return dict(zip(names, value))
    if value is None:
        return {}
    return {"value": value}


# ---------------------------------------------------------------------------


def _replace(obj, **changes):
    from dataclasses import replace

    return replace(obj, **changes)


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t is not None else None


def _port_dict(p: PortValue) -> dict | None:
    if not p.schema and p.json is None:
        return None
    return {
        "schema": p.schema,
        "json": p.json,
        "valid": p.valid,
        "last_modified": _iso(p.last_modified),
    }
