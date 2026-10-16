"""Watch a real-time graph in the browser while it runs.

    python examples/live_realtime.py

Open the printed URL: the viewer follows the latest engine cycle, and new
nested graph instances (map_ keys) appear as the graph creates them.
"""

import random
from datetime import timedelta

from hgraph import TS, TSD, EvaluationMode, compute_node, debug_print, generator, graph, map_, run_graph

from hg_viz import GraphRecorder, serve

SYMBOLS = ["AAPL", "MSFT", "GOOG", "AMZN"]


@generator
def quotes(n: int) -> TSD[str, TS[float]]:
    rnd = random.Random(1)
    px = {s: 100.0 for s in SYMBOLS}
    for i in range(n):
        s = SYMBOLS[: 1 + i // 10][rnd.randrange(min(len(SYMBOLS), 1 + i // 10))]
        px[s] *= 1 + rnd.uniform(-0.01, 0.01)
        yield timedelta(milliseconds=250), {s: round(px[s], 2)}


@compute_node
def signal(px: TS[float]) -> TS[str]:
    return "buy" if px.value < 100 else "sell"


@graph
def app():
    sig = map_(signal, quotes(240))
    debug_print("signals", sig)


if __name__ == "__main__":
    rec = GraphRecorder(min_interval=0.1)
    srv = serve(rec, port=8765, open_browser=True)
    print("hg_viz live viewer:", srv.url)
    run_graph(app, run_mode=EvaluationMode.REAL_TIME, end_time=timedelta(minutes=2),
              life_cycle_observers=rec.observers)
    input("graph finished - press enter to stop the viewer")
