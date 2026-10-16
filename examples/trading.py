"""Record a small trading-style graph and write every visualisation hg_viz offers.

    python examples/trading.py [out_dir]

Writes:
  trading.svg        final state (topology, last values, evaluation stats)
  trading_heat.svg   the same graph coloured by time spent in each node
  trading_cycle.svg  one engine cycle with its data flow highlighted
  trading.html       interactive player: step through every engine cycle
"""

import sys
from pathlib import Path

from hgraph import TS, TSD, compute_node, debug_print, graph, map_, reduce

from hg_viz import record


@compute_node
def notional(px: TS[float], qty: TS[int]) -> TS[float]:
    return px.value * qty.value


@graph
def position_value(px: TS[float], qty: TS[int]) -> TS[float]:
    return notional(px, qty)


@graph
def portfolio(prices: TSD[str, TS[float]], positions: TSD[str, TS[int]], limit: TS[float]) -> TS[bool]:
    values = map_(position_value, prices, positions)
    total = reduce(lambda a, b: a + b, values, 0.0)
    breach = total > limit
    debug_print("total", total)
    return breach


def main(out_dir: str = ".") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rec, result = record(
        portfolio,
        [{"AAPL": 190.0, "MSFT": 410.0}, {"AAPL": 191.5}, {"GOOG": 140.0}, {"MSFT": 405.0}, None],
        [{"AAPL": 100, "MSFT": 50}, None, {"GOOG": 200}, None, {"AAPL": 300}],
        [60_000.0, None, None, None, 100_000.0],
    )
    print("breach per tick:", result)
    print(rec.summary())
    rec.write(out / "trading.svg")
    rec.write(out / "trading_heat.svg", heat="time")
    rec.write(out / "trading_cycle.svg", frame=2)
    rec.write_html(out / "trading.html")
    print(f"wrote {out}/trading*.svg and {out}/trading.html")


if __name__ == "__main__":
    main(*sys.argv[1:])
