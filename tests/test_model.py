from hg_viz import NodeKind


def by_label(model, label):
    found = model.find(label)
    assert found, f"no node {label!r} in {[n.label for n in model.nodes.values()]}"
    return found


def test_simple_topology(simple_recording):
    rec, out = simple_recording
    assert out == [23, 26, 69]
    m = rec.model()
    (double,) = by_label(m, "double")
    assert double.kind == NodeKind.COMPUTE
    assert double.eval_count == 3
    assert double.output.value == 66
    (add_in,) = m.upstream(double.id)
    assert add_in.raw_label == "scalar_add"
    assert {n.label for n in m.upstream(add_in.id)} == {"replay a", "replay b"}
    assert {n.kind for n in m.upstream(add_in.id)} == {NodeKind.PULL_SOURCE}
    record_out = by_label(m, "record out")[0]
    assert record_out.kind == NodeKind.SINK


def test_bundle_field_edges_name_the_output_path(simple_recording):
    rec, _ = simple_recording
    m = rec.model()
    (split,) = by_label(m, "split")
    (edge,) = m.edges_out_of(split.id)
    assert edge.output_path == ("qty",)
    assert edge.input_path == ("rhs",)
    assert edge.label == "qty → rhs"
    assert m.edge_value(edge) == 3


def test_nested_instances_are_labelled_by_key(nested_recording):
    rec, out = nested_recording
    assert out == [30 + 0, 50 + 1, 100 + 4]  # map/reduce + switch
    m = rec.model()
    (map_node,) = by_label(m, "map_")
    assert map_node.kind == NodeKind.NESTED
    instances = [c for c in m.children(map_node.id) if c.is_graph]
    assert sorted(i.label for i in instances) == ["a", "b", "c"]
    (switch,) = by_label(m, "switch_")
    live = [c for c in m.children(switch.id) if c.is_graph and not c.stopped]
    assert [c.label for c in live] == ["key = true"]


def test_keyed_bindings_are_collapsed(nested_recording):
    rec, _ = nested_recording
    m = rec.model()
    (map_node,) = by_label(m, "map_")
    (replay,) = by_label(m, "replay d")
    into = {e.input_path: e for e in m.edges_into(map_node.id) if e.src == replay.id}
    # the TSD binding (+ 3 per-key bindings folded in) and the key-set binding
    assert set(into) == {("0",), ("__keys__",)}
    assert into[("0",)].fan == 3


def test_alive_drops_torn_down_instances(nested_recording):
    rec, _ = nested_recording
    m = rec.model()
    (switch,) = by_label(m, "switch_")
    assert len([c for c in m.children(switch.id) if c.is_graph]) == 2
    alive = m.alive()
    assert len([c for c in alive.children(switch.id) if c.is_graph]) == 1


def test_to_dict_is_json_serialisable(nested_recording):
    import json

    rec, _ = nested_recording
    d = rec.model().to_dict()
    json.dumps(d)
    assert {"nodes", "edges"} <= set(d)
