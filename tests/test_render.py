import json
import re
import urllib.request

import pytest

from hg_viz import DotOptions, has_graphviz, serve, to_dot
from hg_viz.html import topology_payload, viewer_html

needs_dot = pytest.mark.skipif(not has_graphviz(), reason="graphviz not installed")


def test_dot_structure(nested_recording):
    rec, _ = nested_recording
    dot = rec.to_dot()
    assert dot.startswith("digraph hgraph {")
    assert dot.count("subgraph cluster_") >= 1 + 3 + 1  # map_ (+3 keys), reduce, switch_
    assert 'label="map_"' in dot
    assert "->" in dot


def test_collapsed_nested(nested_recording):
    rec, _ = nested_recording
    dot = rec.to_dot(expand_nested=False)
    assert "subgraph cluster_" not in dot
    assert "shape=component" in dot


def test_max_instances(nested_recording):
    rec, _ = nested_recording
    dot = rec.to_dot(max_instances=1)
    assert "+2 more instances" in dot


def test_highlighting(simple_recording):
    rec, _ = simple_recording
    dot = rec.to_dot(frame=1)
    assert "#ea580c" in dot
    plain = rec.to_dot()
    assert "penwidth=2.6" not in plain


def test_heat_and_options(simple_recording):
    rec, _ = simple_recording
    dot = to_dot(rec.model(), DotOptions(heat="count", show_values=False, rankdir="TB", hide_harness=True))
    assert "rankdir=TB" in dot
    assert "replay" not in dot
    assert 'FACE="Courier"' not in dot


def test_labels_are_escaped():
    from hg_viz.dot import _q

    assert _q('a"b\\c\nd') == '"a\\"b\\\\c\\nd"'


@needs_dot
def test_render_svg(nested_recording, tmp_path):
    rec, _ = nested_recording
    for frame in (None, 0, -1):
        svg = rec.write(tmp_path / "g.svg", frame=frame).read_text()
        assert "<svg" in svg
    assert rec.write(tmp_path / "g.png").read_bytes()[:4] == b"\x89PNG"
    assert rec.write(tmp_path / "g.dot").read_text().startswith("digraph")


@needs_dot
def test_viewer_ids_survive_layout(nested_recording):
    rec, _ = nested_recording
    topo = topology_payload(rec)
    assert topo["svg"]
    for key in list(topo["edges"])[:5]:
        pass
    node_ids = set(re.findall(r'<g id="n(\d+)" class="node hgnode', topo["svg"]))
    assert node_ids and node_ids <= set(topo["nodes"])


def test_static_html(nested_recording, tmp_path):
    rec, _ = nested_recording
    html = viewer_html(rec)
    assert "/*__HGVIZ_CONFIG__*/" not in html
    blob = html.split("const CFG = ", 1)[1].split(";\n", 1)[0]
    cfg = json.loads(blob)
    assert cfg["mode"] == "static" and len(cfg["frames"]) == 3
    assert cfg["title"] == "nested"
    path = rec.write_html(tmp_path / "v.html")
    assert path.stat().st_size > 1000


def test_live_server(nested_recording):
    rec, _ = nested_recording
    with serve(rec, port=0) as srv:
        page = urllib.request.urlopen(srv.url).read().decode()
        assert '"mode":"live"' in page
        topo = json.load(urllib.request.urlopen(srv.url + "api/topology?since=-1"))
        assert topo["version"] == rec.topology_version and topo["dot"]
        same = json.load(urllib.request.urlopen(srv.url + f"api/topology?since={topo['version']}"))
        assert same == {"version": topo["version"]}
        frames = json.load(urllib.request.urlopen(srv.url + "api/frames?after=-1"))
        assert len(frames["frames"]) == 3 and frames["running"] is False
        model = json.load(urllib.request.urlopen(srv.url + "api/model"))
        assert model["nodes"]
