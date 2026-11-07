"""Статический dev-сервер для интерфейса без кэширования (правки видны сразу после F5).

Запуск из корня репозитория:  python tools/dev_server.py  →  http://localhost:5173/frontend/index.html
"""
import http.server
import os
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".mjs": "text/javascript"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    # Многопоточный: браузер держит соединения открытыми, однопоточный сервер на этом зависает.
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), NoCacheHandler) as httpd:
        print(f"CQA dev: http://localhost:{PORT}/frontend/index.html")
        httpd.serve_forever()
