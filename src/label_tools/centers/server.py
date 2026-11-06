"""Center-point HTTP routes using the framework's loopback/CSRF boundaries."""

import base64
import io
import json
import logging
from functools import lru_cache
from http.server import ThreadingHTTPServer
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image
from filelock import Timeout

from ..server import Handler as BaseHandler, MAX_BODY
from ..storage import RevisionConflict
from .engine import rgba_labels

WEB = Path(__file__).parent / "web"
VENDOR = Path(__file__).parents[1] / "web/vendor"
LOG = logging.getLogger(__name__)


def png(array):
    buf = io.BytesIO()
    Image.fromarray(array).save(buf, format="PNG")
    return buf.getvalue()


class CenterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, dataset, port=8772):
        self.dataset = dataset
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), CenterHandler)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}"

    @lru_cache(maxsize=128)
    def thumbnail(self, case):
        record = self.dataset.records[case]
        with Image.open(self.dataset.file(record["image"])) as im:
            return png(np.array(im.resize((96, 96))))


class CenterHandler(BaseHandler):
    def bytes_response(self, data, mime):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.trusted():
            return self.respond({"error": "Invalid Host"}, 403)
        url = urlparse(self.path)
        q = parse_qs(url.query)
        ds = self.server.dataset
        try:
            if url.path == "/api/dataset":
                records = []
                for c, r in ds.records.items():
                    states = {}
                    for t in ds.targets:
                        doc = ds.load(c, t)
                        states[t] = dict(reviewed=doc["reviewed"], revision=doc["revision"])
                    records.append(
                        dict(id=c, group=r.get("group", ""), shape_yx=r["shape_yx"], states=states)
                    )
                return self.respond(
                    dict(
                        records=records,
                        targets=ds.targets,
                        token=self.server.token,
                        output=str(ds.root),
                        resolution_nm=ds.resolution,
                        workspace_id=ds.manifest.get("workspace_id"),
                    )
                )
            if url.path == "/api/doc":
                return self.respond(ds.load(q["case"][0], q["target"][0]))
            if url.path == "/api/raw":
                case = q["case"][0]
                if q.get("thumb"):
                    return self.bytes_response(self.server.thumbnail(case), "image/png")
                return self.send_file(ds.file(ds.records[case]["image"]))
            if url.path == "/api/job":
                with ds.jobs_lock:
                    job = dict(ds.jobs[q["id"][0]])
                return self.respond(job)
            assets = {
                "/": "index.html",
                "/app.js": "app.js",
                "/style.css": "style.css",
                "/merge.js": "merge.js",
            }
            if url.path in assets:
                return self.send_file(WEB / assets[url.path])
            if url.path == "/lucide.min.js":
                return self.send_file(VENDOR / "lucide.min.js")
            self.respond({"error": "Not found"}, 404)
        except (KeyError, ValueError, TypeError) as exc:
            self.respond({"error": str(exc)}, 400)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.respond({"error": "Invalid Content-Length"}, 400)
        if not 0 < size <= MAX_BODY:
            return self.respond({"error": "Invalid body size"}, 413)
        if self.headers.get("Transfer-Encoding"):
            return self.respond({"error": "Chunked requests are unsupported"}, 400)
        body = self.rfile.read(size)
        if not self.trusted() or not secrets.compare_digest(
            self.headers.get("X-Annotation-Token", "").encode("utf-8"),
            self.server.token.encode("ascii"),
        ):
            return self.respond({"error": "Invalid local session"}, 403)
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers["Host"]:
            return self.respond({"error": "Cross-origin write rejected"}, 403)
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.respond({"error": "JSON content type required"}, 415)
        try:
            p = json.loads(body)
            if not isinstance(p, dict):
                raise ValueError("Expected JSON object")
            ds = self.server.dataset
            if self.path == "/api/save":
                result = ds.save(p["case"], p["target"], p["doc"])
            elif self.path == "/api/preview":
                r = ds.compute(p["case"], p["target"], p["doc"])
                edge = np.zeros((*r["boundary"].shape, 4), dtype="uint8")
                edge[r["boundary"]] = [255, 193, 57, 255]
                result = dict(
                    instances_png=base64.b64encode(png(rgba_labels(r["instances"]))).decode(),
                    boundary_png=base64.b64encode(png(edge)).decode(),
                    instance_count=r["instance_count"],
                    invalid_points=r["invalid_points"],
                    point_mapping=r["point_mapping"],
                    merge_info=r["merge_info"],
                    invalid_merges=r["invalid_merges"],
                )
            elif self.path == "/api/export":
                result = ds.start_export(p)
            else:
                return self.respond({"error": "Not found"}, 404)
            self.respond(result)
        except RevisionConflict as exc:
            self.respond({"error": str(exc)}, 409)
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            self.respond({"error": str(exc)}, 400)
        except Timeout:
            self.respond({"error": "Workspace busy, retry shortly"}, 503)
        except OSError:
            LOG.exception("Center workspace I/O failed")
            self.respond({"error": "Disk operation failed; check server logs"}, 500)
