"""Loopback-only HTTP service. This is a local tool, not a public deployment server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import re
import secrets
import shutil
from urllib.parse import urlparse

from filelock import Timeout

from .storage import Dataset, RevisionConflict

ASSETS = Path(__file__).parent / "web"
LOG = logging.getLogger(__name__)
MAX_BODY = 4_000_000


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, dataset: Dataset, port: int = 8766):
        self.dataset = dataset
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server: LocalServer

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        super().end_headers()

    def log_message(self, fmt, *args):
        LOG.info(fmt, *args)

    def respond(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def trusted(self):
        host = self.headers.get("Host", "")
        return host in (
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        )

    def send_file(self, path: Path, attachment=False):
        if not path.is_file():
            self.respond({"error": "File not found"}, 404)
            return
        with path.open("rb") as handle:
            self.send_response(200)
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(path.stat().st_size))
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            shutil.copyfileobj(handle, self.wfile)

    def do_GET(self):
        if not self.trusted():
            self.respond({"error": "Invalid Host"}, 403)
            return
        path = urlparse(self.path).path
        dataset = self.server.dataset
        try:
            if path == "/api/dataset":
                self.respond({**dataset.summary(), "token": self.server.token})
                return
            if path.startswith("/api/case/"):
                self.respond(dataset.load(path.removeprefix("/api/case/")))
                return
            assets = {
                "/": "index.html",
                "/app.js": "app.js",
                "/i18n.js": "i18n.js",
                "/style.css": "style.css",
                "/vendor/lucide.min.js": "vendor/lucide.min.js",
            }
            if path in assets:
                self.send_file(ASSETS / assets[path])
                return
            match = re.fullmatch(r"/(images|thumbnails)/([A-Za-z0-9_-]+)\.(png|jpg)", path)
            if match:
                folder, ident, extension = match.groups()
                record = dataset.records.get(ident)
                if record and ((folder, extension) in (("images", "png"), ("thumbnails", "jpg"))):
                    relative = record["image"] if folder == "images" else f"thumbnails/{ident}.jpg"
                    self.send_file(dataset._file(relative))
                    return
            if re.fullmatch(r"/exports/manual_training_[A-Za-z0-9_]+\.zip", path):
                self.send_file(dataset._file("training_exports/" + path.rsplit("/", 1)[1]), True)
                return
            self.respond({"error": "Not found"}, 404)
        except KeyError:
            self.respond({"error": "Unknown image"}, 404)
        except (ValueError, Timeout) as exc:
            self.respond({"error": str(exc)}, 400)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.respond({"error": "Invalid Content-Length"}, 400)
            return
        if not 0 <= size <= MAX_BODY:
            self.respond({"error": "Request exceeds 4 MB"}, 413)
            return
        if self.headers.get("Transfer-Encoding"):
            self.respond({"error": "Chunked requests are not supported"}, 400)
            return
        # Drain bounded request bodies before rejecting authentication, avoiding
        # a TCP reset hiding the JSON error on Windows when the connection closes.
        body = self.rfile.read(size)
        if not self.trusted() or not secrets.compare_digest(
            self.headers.get("X-Annotation-Token", "").encode("utf-8"),
            self.server.token.encode("ascii"),
        ):
            self.respond({"error": "Invalid local session"}, 403)
            return
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers["Host"]:
            self.respond({"error": "Cross-origin write rejected"}, 403)
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            self.respond({"error": "JSON content type required"}, 415)
            return
        try:
            payload = json.loads(body or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("Request must be a JSON object")
            path = urlparse(self.path).path
            if path.startswith("/api/case/"):
                self.respond(self.server.dataset.save(path.removeprefix("/api/case/"), payload))
            elif path == "/api/export":
                self.respond(self.server.dataset.export())
            else:
                self.respond({"error": "Not found"}, 404)
        except RevisionConflict as exc:
            self.respond({"error": str(exc), "code": "revision_conflict"}, 409)
        except KeyError:
            self.respond({"error": "Unknown image"}, 404)
        except (ValueError, TypeError) as exc:
            self.respond({"error": str(exc)}, 400)
        except Timeout:
            self.respond({"error": "Workspace is busy; retry after the other save completes"}, 503)
        except OSError:
            LOG.exception("Write failed")
            self.respond({"error": "Disk write failed; check the local server log"}, 500)
