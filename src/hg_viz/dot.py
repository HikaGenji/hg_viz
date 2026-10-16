"""Render a :class:`~hg_viz.model.GraphModel` as Graphviz DOT source.

Only DOT text is produced here -- turning it into SVG/PNG is done by
:mod:`hg_viz.render` (graphviz ``dot`` binary) or in the browser by the HTML
viewer (viz.js), so this module has no third-party dependencies.

Layout conventions:

* data flows left to right (``rankdir=LR``);
* node fill encodes the node role (source / compute / sink / nested), or the
  evaluation heat when ``heat=`` is requested;
* nested graphs (``map_``, ``reduce``, ``switch_`` ...) are drawn as clusters
  inside the owning node's cluster, one sub-cluster per live instance, labelled
  with the key(s) the instance serves;
* nodes and edges that ticked in the highlighted engine cycle are drawn in
  orange so the data flow of that cycle can be read off the picture.
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .model import GraphModel, NodeKind, VizEdge, VizNode, short_type

__all__ = ("DotOptions", "to_dot", "Palette", "format_value", "format_duration")


@dataclass(frozen=True)
class Palette:
    """Colours used by the renderer (fill, border) per node kind."""

    pull_source: tuple[str, str] = ("#e0f2fe", "#0369a1")
    push_source: tuple[str, str] = ("#ede9fe", "#6d28d9")
    compute: tuple[str, str] = ("#f8fafc", "#475569")
    sink: tuple[str, str] = ("#dcfce7", "#15803d")
    nested: tuple[str, str] = ("#fef3c7", "#b45309")
    cluster: tuple[str, str] = ("#fffbeb", "#f59e0b")
    instance: tuple[str, str] = ("#ffffff", "#fcd34d")
    edge: str = "#64748b"
    muted: str = "#94a3b8"
    text: str = "#0f172a"
    subtext: str = "#64748b"
    active_fill: str = "#fed7aa"
    active: str = "#ea580c"
    heat: tuple[str, ...] = ("#fff7ed", "#ffedd5", "#fed7aa", "#fdba74", "#fb923c", "#f97316", "#ea580c")

    def for_kind(self, kind: str) -> tuple[str, str]:
        return getattr(self, kind, self.compute)


@dataclass(frozen=True)
class DotOptions:
    """Rendering options for :func:`to_dot`."""

    rankdir: str = "LR"
    expand_nested: bool = True
    max_instances: int = 4  # nested graph instances drawn per nested node
    show_values: bool = True
    show_types: bool = True
    show_stats: bool = True
    show_edge_labels: bool = True
    hide_stopped: bool = True  # drop torn-down nested graphs while running
    hide_harness: bool = False  # hide eval_node/run_graph replay & record nodes
    value_width: int = 28
    heat: str | None = None  # None | "count" | "time"
    active_nodes: frozenset[int] = frozenset()
    active_edges: frozenset[str] = frozenset()
    title: str | None = None
    emit_ids: bool = False  # id= attributes for the interactive viewer
    font: str = "Helvetica"
    palette: Palette = field(default_factory=Palette)


def to_dot(model: GraphModel, options: DotOptions | None = None, **overrides) -> str:
    """Return DOT source describing ``model``.

    Keyword overrides are applied on top of ``options`` (``to_dot(m, heat="time")``).
    """
    opts = options or DotOptions()
    if overrides:
        from dataclasses import replace

        if "active_nodes" in overrides:
            overrides["active_nodes"] = frozenset(overrides["active_nodes"])
        if "active_edges" in overrides:
            overrides["active_edges"] = frozenset(overrides["active_edges"])
        opts = replace(opts, **overrides)
    return _DotWriter(model, opts).write()


# ---------------------------------------------------------------------------


class _DotWriter:
    def __init__(self, model: GraphModel, opts: DotOptions):
        if opts.hide_stopped:
            model = model.alive()
        self.model = model
        self.opts = opts
        self.pal = opts.palette
        self.lines: list[str] = []
        self.visible: set[int] = set()
        self.expanded: set[int] = set()  # nested nodes drawn as clusters
        self.hidden_instances: dict[int, int] = {}  # nested node -> hidden count
        self._compute_visibility()
        self._max_heat = max(
            (self._heat_value(n) for n in model.nodes.values() if not n.is_graph), default=0.0
        )

    # -- visibility -------------------------------------------------------
    def _compute_visibility(self) -> None:
        m, o = self.model, self.opts
        for root in m.roots:
            self._visit_graph(root)
        if o.hide_harness:
            self.visible = {
                i for i in self.visible
                if m.nodes[i].raw_label not in ("__harness_replay", "__harness_record")
            }

    def _visit_graph(self, g: VizNode) -> None:
        for child in self.model.children(g.id):
            if child.is_graph:
                continue
            self.visible.add(child.id)
            if child.kind == NodeKind.NESTED and self.opts.expand_nested:
                instances = [c for c in self.model.children(child.id) if c.is_graph]
                if instances:
                    self.expanded.add(child.id)
                    shown = instances[: max(self.opts.max_instances, 0)]
                    if len(instances) > len(shown):
                        self.hidden_instances[child.id] = len(instances) - len(shown)
                    for inst in shown:
                        self.visible.add(inst.id)
                        self._visit_graph(inst)

    def _visible_of(self, node_id: int) -> int | None:
        """The visible entity standing in for ``node_id`` (itself or an
        ancestor); graph entities are never edge endpoints."""
        m = self.model
        cur = m.nodes.get(node_id)
        while cur is not None:
            if cur.id in self.visible and not cur.is_graph:
                return cur.id
            cur = m.nodes.get(cur.parent_id)
        return None

    # -- writing ----------------------------------------------------------
    def write(self) -> str:
        o, p = self.opts, self.pal
        roots = self.model.roots
        title = o.title or (roots[0].label if roots else "hgraph")
        if self.model.evaluation_time is not None and o.title is None:
            title += f"  @ {self.model.evaluation_time.isoformat(sep=' ')}"
        self.lines += [
            "digraph hgraph {",
            f'  graph [rankdir={o.rankdir}, compound=true, newrank=true, nodesep=0.25, ranksep=0.45, '
            f'fontname="{o.font}", fontsize=12, fontcolor="{p.subtext}", labelloc=t, labeljust=l, '
            f'label={_q(title)}, bgcolor="transparent", pad=0.2];',
            f'  node [shape=box, style="rounded,filled", fontname="{o.font}", fontsize=11, '
            f'color="{p.compute[1]}", fillcolor="{p.compute[0]}", fontcolor="{p.text}", margin="0.08,0.04", penwidth=1.2];',
            f'  edge [fontname="{o.font}", fontsize=9, color="{p.edge}", fontcolor="{p.subtext}", '
            f'arrowsize=0.7, penwidth=1.1];',
        ]
        for root in roots:
            self._write_graph_body(root, indent="  ")
        self._write_edges()
        self.lines.append("}")
        return "\n".join(self.lines) + "\n"

    def _write_graph_body(self, g: VizNode, indent: str) -> None:
        for child in self.model.children(g.id):
            if child.id not in self.visible or child.is_graph:
                continue
            if child.id in self.expanded:
                self._write_nested_cluster(child, indent)
            else:
                self._write_node(child, indent)

    def _write_nested_cluster(self, n: VizNode, indent: str) -> None:
        p = self.pal
        fill, border = p.cluster
        self.lines.append(f"{indent}subgraph cluster_{n.id} {{")
        self.lines.append(
            f'{indent}  graph [label={_q(n.label)}, style="rounded,filled,dashed", '
            f'fillcolor="{fill}", color="{border}", fontcolor="{p.nested[1]}", fontsize=11, '
            f'tooltip={_q(n.path)}{self._id_attr("c", n.id)}];'
        )
        self._write_node(n, indent + "  ")
        for inst in self.model.children(n.id):
            if not inst.is_graph or inst.id not in self.visible:
                continue
            ifill, iborder = p.instance
            style = "rounded,filled,dashed" if inst.stopped else "rounded,filled"
            self.lines.append(f"{indent}  subgraph cluster_{inst.id} {{")
            self.lines.append(
                f'{indent}    graph [label={_q(inst.label)}, style="{style}", fillcolor="{ifill}", '
                f'color="{iborder}", fontcolor="{p.subtext}", fontsize=10, '
                f'tooltip={_q(inst.path)}{self._id_attr("c", inst.id)}];'
            )
            self._write_graph_body(inst, indent + "    ")
            if not any(c.id in self.visible for c in self.model.children(inst.id)):
                self.lines.append(f'{indent}    g{inst.id}_empty [label="(empty)", shape=plaintext, style=""];')
            self.lines.append(f"{indent}  }}")
        hidden = self.hidden_instances.get(n.id)
        if hidden:
            self.lines.append(
                f'{indent}  more_{n.id} [label={_q(f"+{hidden} more instances")}, shape=note, '
                f'style="filled", fillcolor="{p.instance[0]}", color="{p.instance[1]}", '
                f'fontcolor="{p.subtext}", fontsize=9];'
            )
        self.lines.append(f"{indent}}}")

    def _write_node(self, n: VizNode, indent: str) -> None:
        o, p = self.opts, self.pal
        fill, border = p.for_kind(n.kind)
        if o.heat:
            fill = self._heat_color(n)
        penwidth = 1.2
        if n.id in o.active_nodes:
            fill, border, penwidth = p.active_fill, p.active, 2.6
        style = "rounded,filled"
        if n.stopped and not all(r.stopped for r in self.model.roots):
            style += ",dashed"
        shape = "component" if n.kind == NodeKind.NESTED else "box"
        if n.kind == NodeKind.PUSH_SOURCE:
            style += ",bold"
        attrs = [
            f"label={self._node_label(n)}",
            f"shape={shape}",
            f'style="{style}"',
            f'fillcolor="{fill}"',
            f'color="{border}"',
            f"penwidth={penwidth}",
            f"tooltip={_q(self._node_tooltip(n))}",
        ]
        if o.emit_ids:
            attrs.append(f'id="n{n.id}"')
            attrs.append(f'class="hgnode kind-{n.kind}"')
        self.lines.append(f"{indent}n{n.id} [{', '.join(attrs)}];")

    def _node_label(self, n: VizNode) -> str:
        o, p = self.opts, self.pal
        rows = [f'<TR><TD ALIGN="LEFT"><B>{_h(n.label)}</B></TD></TR>']
        if o.show_types:
            sub = _kind_text(n.kind)
            if n.output.schema:
                sub += f" → {short_type(n.output.schema)}"
            rows.append(
                f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="8" COLOR="{p.subtext}">{_h(_clip(sub, 40))}</FONT></TD></TR>'
            )
        if o.show_values and n.output.schema:
            text = "= " + format_value(n.output.value, o.value_width) if n.output.valid else "= ∅"
            rows.append(
                f'<TR><TD ALIGN="LEFT"{self._id_attr("v", n.id, html_attr=True)}>'
                f'<FONT FACE="Courier" POINT-SIZE="10">{_h(text)}</FONT></TD></TR>'
            )
        if o.show_stats:
            stats = f"×{n.eval_count}"
            if n.eval_time:
                stats += f" · {format_duration(n.eval_time)}"
            if n.eval_failures:
                stats += f" · {n.eval_failures} failed"
            rows.append(
                f'<TR><TD ALIGN="LEFT"{self._id_attr("s", n.id, html_attr=True)}>'
                f'<FONT POINT-SIZE="8" COLOR="{p.subtext}">{_h(stats)}</FONT></TD></TR>'
            )
        return '<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1">' + "".join(rows) + "</TABLE>>"

    def _node_tooltip(self, n: VizNode) -> str:
        parts = [n.path or n.raw_label, f"kind: {n.kind}", f"evaluations: {n.eval_count}"]
        if n.implementation:
            parts.append(f"impl: {n.implementation}")
        if n.output.schema:
            parts.append(f"output: {n.output.schema} = {_clip(n.output.json or '', 200)}")
        if n.scalars.json and n.scalars.json not in ("[]", "{}"):
            parts.append(f"scalars: {_clip(n.scalars.json, 200)}")
        return "\n".join(parts)

    def _write_edges(self) -> None:
        o, p = self.opts, self.pal
        seen: dict[tuple, list] = {}
        order: list[tuple] = []
        for e in self.model.edges:
            src, dst = self._visible_of(e.src), self._visible_of(e.dst)
            if src is None or dst is None or src == dst:
                continue
            k = (src, dst, e.label if (src, dst) == (e.src, e.dst) else e.input_name)
            if k not in seen:
                seen[k] = []
                order.append(k)
            seen[k].append(e)
        # An expanded nested node's own input is redundant when its inner
        # nodes already show a binding from the same source.
        inner_sources: dict[int, set[int]] = {}
        for (src, dst, _), _edges in seen.items():
            for anc in self.model.ancestors(dst):
                if anc in self.expanded:
                    inner_sources.setdefault(anc, set()).add(src)
        for k in order:
            src, dst, label = k
            edges = seen[k]
            if dst in self.expanded and src in inner_sources.get(dst, ()):
                continue
            active = any(e.key in o.active_edges for e in edges)
            attrs = []
            if o.show_edge_labels and label:
                attrs.append(f"label={_q(label)}")
            value = self.model.edge_value(edges[0])
            tip = f"{self.model.nodes[src].label} → {self.model.nodes[dst].label}.{edges[0].input_name}"
            if value is not None:
                tip += f"\n{_clip(json.dumps(value, default=str), 200)}"
            attrs.append(f"tooltip={_q(tip)}")
            attrs.append(f"labeltooltip={_q(tip)}")
            if active:
                attrs += [f'color="{p.active}"', "penwidth=2.4", f'fontcolor="{p.active}"']
            if any(self.model.nodes[i].stopped for i in (src, dst)) and not all(r.stopped for r in self.model.roots):
                attrs.append("style=dashed")
            if o.emit_ids:
                attrs.append(f"id={_q('e:' + '|'.join(e.key for e in edges))}")
                attrs.append('class="hgedge"')
            self.lines.append(f"  n{src} -> n{dst} [{', '.join(attrs)}];")
        self._write_collect_edges()

    def _write_collect_edges(self) -> None:
        """Dotted edges from each nested instance's result node(s) to the
        owning nested node: the instance outputs are what the nested node
        publishes (``map_`` collects them into its TSD, ``switch_`` forwards
        the active branch ...)."""
        o, p = self.opts, self.pal
        m = self.model
        for nested_id in sorted(self.expanded):
            for inst in m.children(nested_id):
                if not inst.is_graph or inst.id not in self.visible:
                    continue
                inner = [c for c in m.children(inst.id) if c.id in self.visible and not c.is_graph]
                inner_ids = {c.id for c in inner}
                consumed = {e.src for e in m.edges if e.src in inner_ids and e.dst in inner_ids}
                for c in inner:
                    if c.id in consumed or not c.output.schema:
                        continue
                    attrs = ["style=dotted", f'color="{p.muted}"', "arrowhead=empty", "penwidth=1.0",
                             f"tooltip={_q(f'{c.label} → {m.nodes[nested_id].label} (instance {inst.label})')}"]
                    if c.id in o.active_nodes:
                        attrs += [f'color="{p.active}"', "penwidth=2.0"]
                    if o.emit_ids:
                        attrs += [f'id="x:{c.id}"', 'class="hgedge collect"']
                    self.lines.append(f"  n{c.id} -> n{nested_id} [{', '.join(attrs)}];")

    # -- helpers ----------------------------------------------------------
    def _id_attr(self, prefix: str, node_id: int, html_attr: bool = False) -> str:
        if not self.opts.emit_ids:
            return ""
        return f' ID="{prefix}{node_id}"' if html_attr else f', id="{prefix}{node_id}"'

    def _heat_value(self, n: VizNode) -> float:
        if self.opts.heat == "time":
            return n.eval_time / timedelta(microseconds=1)
        if self.opts.heat == "count":
            return float(n.eval_count)
        return 0.0

    def _heat_color(self, n: VizNode) -> str:
        ramp = self.pal.heat
        if self._max_heat <= 0:
            return ramp[0]
        frac = math.log1p(self._heat_value(n)) / math.log1p(self._max_heat)
        return ramp[min(int(frac * (len(ramp) - 1) + 0.5), len(ramp) - 1)]


# ---------------------------------------------------------------------------


def _kind_text(kind: str) -> str:
    return {
        NodeKind.PULL_SOURCE: "source",
        NodeKind.PUSH_SOURCE: "push source",
        NodeKind.COMPUTE: "compute",
        NodeKind.SINK: "sink",
        NodeKind.NESTED: "nested",
    }.get(kind, kind)


def format_value(value: Any, width: int = 28) -> str:
    """Compact single-line rendering of a captured JSON value."""
    if value is None:
        text = "null"
    elif isinstance(value, str):
        text = repr(value)
    elif isinstance(value, float):
        text = f"{value:.6g}"
    else:
        try:
            text = json.dumps(value, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            text = str(value)
    return _clip(text, width)


def format_duration(d: timedelta) -> str:
    us = d / timedelta(microseconds=1)
    if us < 1000:
        return f"{us:.0f}µs"
    if us < 1_000_000:
        return f"{us / 1000:.1f}ms"
    return f"{us / 1_000_000:.2f}s"


def _clip(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: max(width - 1, 1)] + "…"


def _h(text: str) -> str:
    return html.escape(text, quote=True)


def _q(text: str) -> str:
    """A double-quoted DOT string."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
