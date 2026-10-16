import pytest
from hgraph import TS, TSB, TSD, TimeSeriesSchema, compute_node, graph, map_, reduce, switch_

from hg_viz import GraphRecorder, record


class Price(TimeSeriesSchema):
    px: TS[float]
    qty: TS[int]


@compute_node
def split(x: TS[int]) -> TSB[Price]:
    return {"px": x.value * 1.5, "qty": x.value}


@compute_node
def double(x: TS[int]) -> TS[int]:
    return x.value * 2


@graph
def simple(a: TS[int], b: TS[int]) -> TS[int]:
    return double(a + b) + split(a).qty


@graph
def per_key(v: TS[int]) -> TS[int]:
    return v * 10


@graph
def nested(d: TSD[str, TS[int]], x: TS[int]) -> TS[int]:
    m = map_(per_key, d)
    s = switch_(x > 2, {True: lambda v: v + 1, False: lambda v: v - 1}, x)
    return reduce(lambda a, b: a + b, m, 0) + s


@pytest.fixture
def simple_recording():
    rec, out = record(simple, [1, 2, 3], [10, None, 30])
    return rec, out


@pytest.fixture
def nested_recording():
    rec, out = record(nested, [{"a": 1, "b": 2}, {"a": 3}, {"c": 5}], [1, 2, 3])
    return rec, out
