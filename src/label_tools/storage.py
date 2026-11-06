"""Authoritative geometry, revision conflicts, derived labels, and reviewed exports."""

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import threading
import uuid
import zipfile

from filelock import FileLock
import numpy as np
from PIL import Image
import tifffile

from . import __version__
from .dataset import atomic_json, contained_path, load_manifest, read_image, sha256
from .geometry import rasterize, validate_document


class RevisionConflict(ValueError):
    """The browser edited a revision that has already been replaced."""


def spatial_overlap(records: list[dict]) -> list[list[str]]:
    pairs = []
    for index, a in enumerate(records):
        if a.get("origin_xy") is None or not a.get("spatial_frame"):
            continue
        for b in records[:index]:
            if b.get("origin_xy") is None or b.get("spatial_frame") != a["spatial_frame"]:
                continue
            if a.get("resolution_nm") != b.get("resolution_nm"):
                continue
            ax, ay = a["origin_xy"]
            bx, by = b["origin_xy"]
            if (
                ax < bx + b["width"]
                and bx < ax + a["width"]
                and ay < by + b["height"]
                and by < ay + a["height"]
            ):
                pairs.append([a["id"], b["id"]])
    return pairs


class Dataset:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        if (self.root / "IMPORT_INCOMPLETE").exists():
            raise ValueError("Dataset import did not finish")
        self.manifest = load_manifest(self.root)
        self.records = {record["id"]: record for record in self.manifest["records"]}
        self.thread_lock = threading.RLock()
        self.file_lock = FileLock(contained_path(self.root, ".label-tools.lock"), timeout=10)
        for name in (
            "annotations",
            "annotation_history",
            "labels",
            "ignore_masks",
            "training_exports",
        ):
            contained_path(self.root, name).mkdir(exist_ok=True)

    def _file(self, relative: str) -> Path:
        return contained_path(self.root, relative)

    def load(self, ident: str) -> dict:
        if ident not in self.records:
            raise KeyError("Unknown image")
        path = self._file(f"annotations/{ident}.json")
        if not path.exists():
            return {"id": ident, "revision": 0, "status": "draft", "polygons": [], "notes": ""}
        result = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(result, dict)
            or result.get("id") != ident
            or type(result.get("revision")) is not int
            or result["revision"] < 1
        ):
            raise ValueError(f"Corrupt annotation metadata: {ident}")
        validate_document(result, self.records[ident])
        return result

    def summary(self) -> dict:
        with self.thread_lock, self.file_lock:
            records = []
            for record in self.records.values():
                doc = self.load(record["id"])
                records.append(
                    dict(
                        record, annotation_status=doc["status"], instance_count=len(doc["polygons"])
                    )
                )
            return {
                "dataset": self.manifest["dataset"],
                "workspace_id": self.manifest["workspace_id"],
                "target": self.manifest["target"],
                "language": self.manifest["language"],
                "records": records,
                "save_root": str(self.root),
                "version": __version__,
            }

    def save(self, ident: str, value: dict) -> dict:
        if not isinstance(value, dict):
            raise ValueError("Annotation must be a JSON object")
        with self.thread_lock, self.file_lock:
            old = self.load(ident)
            revision = value.get("revision")
            if type(revision) is not int or revision != old["revision"]:
                raise RevisionConflict(
                    "A newer revision exists. Keep your draft and reload before retrying."
                )
            record = self.records[ident]
            doc = validate_document(value, record)
            labels, ignore = rasterize(doc, record)
            doc.update(
                id=ident,
                revision=revision + 1,
                schema_version=1,
                updated_at=datetime.now(timezone.utc).isoformat(),
                resolution_nm=record.get("resolution_nm"),
                target=self.manifest["target"],
                annotation_method="manual_polygon",
                app_version=__version__,
            )
            if record.get("origin_xy") is not None:
                doc.update(origin_xy=record["origin_xy"], spatial_frame=record.get("spatial_frame"))
            stamp = f"{ident}_r{doc['revision']:05d}_{uuid.uuid4().hex}"
            atomic_json(self._file(f"annotation_history/{stamp}.json"), doc)
            # Derived masks can be rebuilt. Commit authoritative geometry last.
            for folder, array in (("labels", labels), ("ignore_masks", ignore)):
                temp = self._file(f"{folder}/{ident}.{uuid.uuid4().hex}.tmp.tif")
                try:
                    tifffile.imwrite(
                        temp,
                        array,
                        compression="deflate",
                        metadata={
                            "axes": "YX",
                            "resolution_nm": record.get("resolution_nm"),
                            "annotation_status": doc["status"],
                            "revision": doc["revision"],
                        },
                    )
                    os.replace(temp, self._file(f"{folder}/{ident}.tif"))
                finally:
                    temp.unlink(missing_ok=True)
            atomic_json(self._file(f"annotations/{ident}.json"), doc)
            return doc

    def verify(self) -> dict:
        checks = []
        for record in self.records.values():
            raw_path = self._file(record["raw_tiff"])
            preview_path = self._file(record["image"])
            raw = read_image(raw_path)
            with Image.open(preview_path) as image:
                preview = np.asarray(image)
            shape = (record["height"], record["width"])
            if raw.shape[:2] != shape or preview.shape[:2] != shape or preview.dtype != np.uint8:
                raise ValueError(f"Image dimensions/preview dtype mismatch: {record['id']}")
            for path, expected in (
                (raw_path, record.get("raw_sha256")),
                (preview_path, record.get("preview_sha256", record.get("sha256"))),
            ):
                if expected and sha256(path) != expected:
                    raise ValueError(f"Image checksum mismatch: {record['id']}")
            checks.append(
                {"id": record["id"], "shape_yx": list(shape), "raw_dtype": str(raw.dtype)}
            )
        return {"status": "PASS", "images": len(checks), "checks": checks}

    def export(self) -> dict:
        with self.thread_lock, self.file_lock:
            docs = [self.load(ident) for ident in self.records]
            docs = [doc for doc in docs if doc["status"] in ("complete", "negative")]
            if not docs:
                raise ValueError("No completed annotations or confirmed negatives to export")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            name = f"manual_training_{stamp}_{uuid.uuid4().hex[:12]}.zip"
            final = self._file(f"training_exports/{name}")
            temporary = final.with_suffix(".tmp")
            entries = []
            try:
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                    for doc in docs:
                        ident = doc["id"]
                        record = self.records[ident]
                        labels, ignore = rasterize(validate_document(doc, record), record)
                        raw_path = self._file(record["raw_tiff"])
                        if record.get("raw_sha256") and sha256(raw_path) != record["raw_sha256"]:
                            raise ValueError(f"Original image changed: {ident}")
                        raw = read_image(raw_path)
                        if raw.shape[:2] != labels.shape:
                            raise ValueError(f"Image and label dimensions differ: {ident}")
                        for prefix, array in (("labels", labels), ("ignore_masks", ignore)):
                            buffer = io.BytesIO()
                            tifffile.imwrite(
                                buffer,
                                array,
                                compression="deflate",
                                metadata={
                                    "axes": "YX",
                                    "resolution_nm": record.get("resolution_nm"),
                                },
                            )
                            archive.writestr(f"{prefix}/{ident}.tif", buffer.getvalue())
                        archive.write(raw_path, f"images/{ident}.tif")
                        archive.writestr(
                            f"annotations/{ident}.json",
                            json.dumps(doc, indent=2, ensure_ascii=False),
                        )
                        entries.append(
                            {
                                **{
                                    key: value
                                    for key, value in record.items()
                                    if key
                                    not in (
                                        "preview_sha256",
                                        "sha256",
                                        "display_window",
                                        "annotation_status",
                                    )
                                },
                                "image": f"images/{ident}.tif",
                                "raw_tiff": f"images/{ident}.tif",
                                "label": f"labels/{ident}.tif",
                                "ignore_mask": f"ignore_masks/{ident}.tif",
                                "status": doc["status"],
                                "revision": doc["revision"],
                                "instance_count": len(doc["polygons"]),
                                "partial_instances": [
                                    p["id"] for p in doc["polygons"] if p["partial"]
                                ],
                            }
                        )
                    archive.writestr(
                        "manifest.json",
                        json.dumps(
                            {
                                "schema_version": 1,
                                "app_version": __version__,
                                "target": self.manifest["target"],
                                "records": entries,
                                "overlapping_pairs": spatial_overlap(entries),
                                "overlap_scope": "Only records with matching spatial_frame and resolution_nm and known origin_xy are checked; all other overlap is unknown.",
                                "label_semantics": "uint16: 0 background; positive IDs are distinct instances within each image. ignore_masks=255 means partial target, not negative background.",
                                "split_policy": "Keep overlapping fields in one split. Verify specimen provenance and overlap with earlier training data independently.",
                            },
                            indent=2,
                            ensure_ascii=False,
                        ),
                    )
                os.replace(temporary, final)
            finally:
                temporary.unlink(missing_ok=True)
            return {
                "file": name,
                "cases": len(entries),
                "download": "/exports/" + name,
                "path": str(final),
            }
