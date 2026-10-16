"""A tiny live viewer for running (e.g. real-time) graphs.

``serve(recorder)`` starts a background HTTP server; open the printed URL
while the graph runs and the viewer follows the latest engine cycle, picking up
new nodes (``map_`` keys appearing, ``switch_`` branches changing) as they are
created.  Standard library only.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from .dot import DotOptions
from .html import topology_payload, viewer_html

if TYPE_CHECKING:  # pragma: no cover
    from .recorder import GraphRecorder

__all__ = ("VizServer", "serve")


class VizServer:
    """Serves the live viewer for ``recorder``; see :func:`serve`."""

    def __init__(self, recorder: "GraphRecorder", host: str = "127.0.0.1", port: int = 0,
                 options: DotOptions | None = None, title: str | None = None, poll_ms: int = 1000):
        self.recorder = recorder
        self.options = options
        self.title = title
        self.poll_ms = poll_ms
        self._topology: dict | None = None
        self._topology_lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence default stderr logging
                pass

            def do_GET(self):
                url = urlparse(self.path)
                q = parse_qs(url.query)
                if url.path in ("/", "/index.html"):
                    body = viewer_html(server.recorder, title=server.title, live=True,
                                       poll_ms=server.poll_ms).encode()
                    return self._send(body, "text/html; charset=utf-8")
                if url.path == "/api/topology":
                    since = int(q.get("since", ["-1"])[0])
                    topo = server.topology()
                    if topo["version"] == since:
                        topo = {"version": since}
                    return self._json(topo)
                if url.path == "/api/frames":
                    after = int(q.get("after", ["-1"])[0])
                    frames = server.recorder.payloads(after=after, limit=500)
                    return self._json({"frames": frames, "running": server.recorder.running})
                if url.path == "/api/model":
                    return self._json(server.recorder.model().to_dict())
                self.send_error(404)

            def _json(self, obj):
                self._send(json.dumps(obj, separators=(",", ":"), default=str).encode(), "application/json")

            def _send(self, body: bytes, ctype: str):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="hg_viz-server", daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/"

    def topology(self) -> dict:
        """The (cached) layout for the recorder's current topology version."""
        with self._topology_lock:
            if self._topology is None or self._topology["version"] != self.recorder.topology_version:
                self._topology = topology_payload(self.recorder, self.options)
            return self._topology

    def start(self) -> "VizServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()


def serve(recorder: "GraphRecorder", port: int = 8765, host: str = "127.0.0.1", *, open_browser: bool = False,
          options: DotOptions | None = None, title: str | None = None, poll_ms: int = 1000) -> VizServer:
    """Start a live viewer for ``recorder`` in a background thread.

    ::

        rec = GraphRecorder(min_interval=0.2)
        srv = serve(rec, port=8765)
        print("open", srv.url)
        run_graph(my_graph, run_mode=EvaluationMode.REAL_TIME,
                  life_cycle_observers=rec.observers, end_time=timedelta(minutes=5))
    """
    server = VizServer(recorder, host=host, port=port, options=options, title=title, poll_ms=poll_ms).start()
    if open_browser:
        webbrowser.open(server.url)
    return server
