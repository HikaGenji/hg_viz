import json

from hg_viz import GraphRecorder, record


def test_one_frame_per_cycle(simple_recording):
    rec, _ = simple_recording
    assert [f.cycle for f in rec.frames] == [1, 2, 3]
    times = [f.evaluation_time for f in rec.frames]
    assert times == sorted(times)


def test_frames_explain_the_data_flow(simple_recording):
    rec, _ = simple_recording
    m = rec.model()
    replay_b = m.find("replay b")[0]
    split = m.find("split")[0]
    # cycle 2: b did not tick (None), a did
    f2 = rec.frames[1]
    assert replay_b.id not in f2.ticked
    assert split.id in f2.evaluated
    assert not [k for k in f2.active_edges if int(k.split(">")[0]) == replay_b.id]
    # cycle 3: both ticked
    assert replay_b.id in rec.frames[2].ticked


def test_every_and_max_frames():
    from conftest import simple

    rec, _ = record(simple, [1, 2, 3, 4, 5, 6], [1] * 6, recorder=GraphRecorder(every=2, max_frames=2))
    assert [f.cycle for f in rec.frames] == [4, 6]
    assert [p["cycle"] for p in rec.payloads()] == [4, 6]


def test_payloads_are_json(nested_recording):
    rec, _ = nested_recording
    payloads = rec.payloads()
    assert len(payloads) == 3
    text = json.dumps(payloads)
    assert '"ev":1' in text.replace(" ", "")
    assert rec.payloads(after=payloads[0]["i"])[0]["i"] == payloads[1]["i"]


def test_union_model_keeps_torn_down_instances(nested_recording):
    rec, _ = nested_recording
    union = rec.union_model()
    switch = union.find("switch_")[0]
    assert len([c for c in union.children(switch.id) if c.is_graph]) == 2


def test_run_graph_mode():
    from datetime import datetime, timedelta

    from hgraph import const, debug_print, graph

    @graph
    def g():
        debug_print("x", const(1) + const(2))

    rec, _ = record(g, start_time=datetime(2024, 1, 1), end_time=datetime(2024, 1, 1) + timedelta(seconds=1))
    assert len(rec.frames) == 1
    assert rec.model().find("scalar_add")[0].output.value == 3


def test_summary(simple_recording):
    rec, _ = simple_recording
    assert "evals" in rec.summary()
