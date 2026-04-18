#!/usr/bin/env python3
"""BeoSound 5c HTTP server with no-cache headers.

Drop-in replacement for `python3 -m http.server 8000`.
Adds Cache-Control: no-store to every response so Chromium's
in-memory HTTP cache never serves stale files (playlist JSON,
JS, CSS, etc.).  This is appropriate for a local kiosk app.
"""

import http.server
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def _redirect_config(self):
        self.send_response(302)
        self.send_header('Location', '/softarc/config.html')
        self.end_headers()

    def do_GET(self):
        if self.path == '/config':
            self._redirect_config()
            return
        super().do_GET()

    def do_HEAD(self):
        if self.path == '/config':
            self._redirect_config()
            return
        super().do_HEAD()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    with http.server.HTTPServer(("", PORT), NoCacheHandler) as httpd:
        print(f"Serving on port {PORT} (no-cache)")
        httpd.serve_forever()
