"""Portable center-refinement workspaces and lossless, provenance-aware exports."""

import json
import secrets
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
import tifffile
from filelock import FileLock
from scipy import ndimage as ndi

from ..dataset import (
    ID_PATTERN,
    IMAGE_SUFFIXES,
    atomic_json,
    checked_resolution,
    contained_path,
    display_image,
    read_image,
    sha256,
)
from ..storage import RevisionConflict
from .engine import edge_ignore, refine, validate_doc

SCHEMA = "label-tools-centers-v1"


def import_centers(images, masks, output, *, instances=None, resolution_nm=None, target="Objects"):
    images, masks, output = Path(images).resolve(), Path(masks).resolve(), Path(output).resolve()
    checked_resolution(resolution_nm)
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Target name is required")
    paths = sorted(
        p for p in images.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    if not paths:
        raise ValueError("No images found")
    if len({p.stem.casefold() for p in paths}) != len(paths):
        raise ValueError("Image stems must be unique")

    def paired(folder, stem):
        candidates = [
            p
            for p in Path(folder).iterdir()
            if p.is_file()
            and p.stem.casefold() == stem.casefold()
            and p.suffix.lower() in IMAGE_SUFFIXES
        ]
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one mask for {stem}")
        return candidates[0]

    # Resolve every pairing before creating the workspace; never invent missing negatives.
    inputs = [
        (p, paired(masks, p.stem), paired(instances, p.stem) if instances else None) for p in paths
    ]
    output.mkdir(parents=True, exist_ok=False)
    marker = output / "IMPORT_INCOMPLETE"
    marker.touch()
    for name in ["images", "raw", "foreground", "instances"]:
        (output / name).mkdir()
    records = []
    for i, (ip, mp, lp) in enumerate(inputs, 1):
        raw = read_image(ip)
        mask = read_image(mp)
        if raw.ndim != 2 or mask.ndim != 2 or raw.shape != mask.shape:
            raise ValueError(f"{ip.name}: expected aligned 2D grayscale and mask")
        if not set(np.unique(mask)).issubset({0, 1, 255}):
            raise ValueError("Foreground masks must use binary 0/1 or 0/255 values")
        fg = mask > 0
        labels = read_image(lp) if lp else ndi.label(fg, structure=np.ones((3, 3)))[0]
        if (
            labels.shape != fg.shape
            or labels.dtype.kind not in "ui"
            or labels.min() < 0
            or labels.max() > 2**32 - 1
        ):
            raise ValueError("Instance masks must be aligned uint32-compatible integers")
        if not np.array_equal(labels > 0, fg):
            raise ValueError("Instance foreground differs from binary mask")
        ident = f"IMG{i:04d}"
        preview, window = display_image(raw)
        Image.fromarray(preview).save(output / "images" / f"{ident}.png")
        for name, a in [
            ("raw", raw),
            ("foreground", fg.astype("uint8")),
            ("instances", labels.astype("uint32")),
        ]:
            tifffile.imwrite(
                output / name / f"{ident}.tif", a, compression="deflate", metadata={"axes": "YX"}
            )
        records.append(
            dict(
                id=ident,
                group="",
                shape_yx=list(raw.shape),
                image=f"images/{ident}.png",
                raw_tiff=f"raw/{ident}.tif",
                source_filename=ip.name,
                display_window=window,
                source_sha256={
                    "raw": sha256(ip),
                    "foreground": sha256(mp),
                    "instances": sha256(lp) if lp else None,
                },
                targets={
                    "object": dict(
                        foreground=f"foreground/{ident}.tif", instances=f"instances/{ident}.tif"
                    )
                },
            )
        )
    data = dict(
        schema=SCHEMA,
        workspace_id=uuid.uuid4().hex,
        resolution_nm=resolution_nm,
        targets={"object": target},
        records=records,
    )
    atomic_json(output / "manifest.json", data)
    marker.unlink()
    return data


def create_center_demo(output):
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for f in ["raw", "masks"]:
            (root / f).mkdir()
        y, x = np.mgrid[:512, :640]
        rng = np.random.default_rng(2026)
        for i in range(3):
            fg = (
                ((x - 245) ** 2 + (y - 260) ** 2 < 85**2)
                | ((x - 365) ** 2 + (y - 260) ** 2 < 85**2)
                | ((x - 120) ** 2 + (y - 95) ** 2 < 37**2)
            )
            raw = np.clip(
                190 - fg * 95 + 18 * np.sin(x / 11 + y / 17) + rng.normal(0, 7, x.shape), 0, 255
            ).astype("uint8")
            tifffile.imwrite(root / "raw" / f"synthetic_{i}.tif", raw)
            tifffile.imwrite(root / "masks" / f"synthetic_{i}.tif", fg.astype("uint8"))
        return import_centers(
            root / "raw", root / "masks", output, resolution_nm=20, target="Synthetic objects"
        )


class CenterDataset:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if (self.root / "IMPORT_INCOMPLETE").exists():
            raise ValueError("Incomplete import")
        self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("schema") != SCHEMA:
            raise ValueError("Not a center-refinement workspace")
        self.resolution = checked_resolution(self.manifest.get("resolution_nm"))
        self.targets = self.manifest.get("targets", {})
        if not self.targets or any(
            not ID_PATTERN.fullmatch(k) or not isinstance(v, str) or not v
            for k, v in self.targets.items()
        ):
            raise ValueError("Invalid target definitions")
        self.records = {}
        ids = set()
        for r in self.manifest.get("records", []):
            ident = r.get("id")
            if (
                not isinstance(ident, str)
                or not ID_PATTERN.fullmatch(ident)
                or ident.casefold() in ids
            ):
                raise ValueError("Invalid or duplicate image ID")
            ids.add(ident.casefold())
            shape = r.get("shape_yx")
            if (
                not isinstance(shape, list)
                or len(shape) != 2
                or any(type(n) is not int or n < 2 for n in shape)
                or np.prod(shape) > 16777216
            ):
                raise ValueError("Invalid image shape")
            for key in ["image", "raw_tiff"]:
                self.file(r.get(key))
            if set(r.get("targets", {})) != set(self.targets):
                raise ValueError("Each image must define every target")
            for config in r["targets"].values():
                if not config.get("foreground"):
                    raise ValueError("Missing foreground")
                for key, value in config.items():
                    if key not in (
                        "foreground",
                        "instances",
                        "strict_ignore",
                        "pseudo_ignore",
                        "source_ignore",
                        "conflict_ignore",
                    ):
                        raise ValueError("Unknown target file role")
                    self.file(value)
            self.records[ident] = r
        if not self.records:
            raise ValueError("Workspace has no images")
        self.jobs = {}
        self.jobs_lock = threading.Lock()
        self.lock = FileLock(str(self.root / "centers.lock"), timeout=20)
        self.export_lock = threading.Lock()

    def file(self, relative):
        p = contained_path(self.root, relative)
        if not p.is_file():
            raise ValueError("Missing workspace file")
        return p

    def key(self, case, target):
        if case not in self.records or target not in self.targets:
            raise ValueError("Unknown image or target")
        return case + "_" + target

    def doc_path(self, case, target):
        # Resolve generated paths too, to reject pre-existing escaping symlinks.
        return contained_path(self.root, "center_annotations/" + self.key(case, target) + ".json")

    def load(self, case, target):
        p = self.doc_path(case, target)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return dict(
            revision=0,
            points=[],
            strokes=[],
            boundary_width=2,
            image_weight=0.15,
            reviewed=False,
            notes="",
        )

    def save(self, case, target, payload):
        if type(payload.get("revision")) is not int or payload["revision"] < 0:
            raise ValueError("Invalid revision")
        doc = validate_doc(payload, self.arrays(case, target)[0].shape)
        with self.lock:
            current = self.load(case, target)
            if payload["revision"] != current["revision"]:
                raise RevisionConflict(
                    "This annotation changed in another tab. Reload before editing."
                )
            if current["revision"]:
                p = contained_path(
                    self.root,
                    f"center_history/{self.key(case, target)}/r{current['revision']:06d}.json",
                )
                p.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(p, current)
            doc.update(
                revision=current["revision"] + 1,
                updated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                case=case,
                target=target,
                resolution_nm=self.resolution,
                axis="YX; points XY",
            )
            p = self.doc_path(case, target)
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(p, doc)
        return doc

    @lru_cache(maxsize=4)
    def arrays(self, case, target):
        self.key(case, target)
        r = self.records[case]
        cfg = r["targets"][target]
        raw = np.array(Image.open(self.file(r["image"])))
        fg = read_image(self.file(cfg["foreground"])) > 0
        lab = (
            read_image(self.file(cfg["instances"]))
            if cfg.get("instances")
            else ndi.label(fg, structure=np.ones((3, 3)))[0]
        )
        if (
            raw.ndim != 2
            or raw.dtype != np.uint8
            or raw.shape != tuple(r["shape_yx"])
            or lab.shape != fg.shape
            or raw.shape != fg.shape
        ):
            raise ValueError("Image/mask grid mismatch")
        if (
            lab.dtype.kind not in "ui"
            or lab.min() < 0
            or lab.max() > 2**32 - 1
            or not np.array_equal(lab > 0, fg)
        ):
            raise ValueError("Invalid instance labels")
        return raw, fg, lab.astype("uint32")

    def compute(self, case, target, doc):
        return refine(*self.arrays(case, target), doc)

    def export_pair(self, case, target, doc, root):
        result = self.compute(case, target, doc)
        if result["invalid_points"]:
            raise ValueError(f"{case}/{target}: invalid points {result['invalid_points']}")
        record = self.records[case]
        cfg = record["targets"][target]
        shape = result["foreground"].shape

        def mask(key, default=False):
            if key not in cfg:
                return np.full(shape, default, dtype=bool)
            array = read_image(self.file(cfg[key]))
            if array.shape != shape:
                raise ValueError("Ignore mask shape mismatch")
            return array > 0

        hard = mask("source_ignore") | mask("conflict_ignore") | edge_ignore(result["instances"])
        strict = hard.copy() if doc["reviewed"] else mask("strict_ignore", True) | hard
        if not doc["reviewed"]:
            if doc["points"]:
                strict |= result["boundary"]
            if doc["strokes"]:
                strict |= ndi.binary_dilation(
                    result["foreground"] != self.arrays(case, target)[1],
                    iterations=doc["boundary_width"] + 1,
                )
        pseudo = mask("pseudo_ignore") | hard
        arrays = {
            k: result[k].astype("uint8") for k in ["foreground", "boundary", "interior", "classes"]
        }
        arrays.update(
            instances=result["instances"].astype("uint32"),
            training_ignore_strict=strict.astype("uint8"),
            training_ignore_pseudo=pseudo.astype("uint8"),
            training_target_strict=np.where(strict, 255, result["classes"]).astype("uint8"),
        )
        out = root / case / target
        out.mkdir(parents=True, exist_ok=True)
        for name, array in arrays.items():
            tifffile.imwrite(
                out / f"{name}.tif",
                array,
                compression="deflate",
                metadata={"axes": "YX", "resolution_nm": self.resolution},
            )
        rawpath = root / case / "raw.tif"
        if not rawpath.exists():
            import shutil

            shutil.copy2(self.file(record["raw_tiff"]), rawpath)
        atomic_json(out / "annotation.json", doc)
        meta = dict(
            case=case,
            target=target,
            revision=doc["revision"],
            resolution_nm=self.resolution,
            shape_yx=list(shape),
            instance_count=result["instance_count"],
            point_mapping=result["point_mapping"],
            human_reviewed=doc["reviewed"],
            boundary_width_px=doc["boundary_width"],
            foreground_includes_boundary=True,
            source_files_modified=False,
            semantics={
                "classes": {"0": "background", "1": "interior", "2": "boundary"},
                "ignore_mask_value": 1,
                "strict_target_ignore_value": 255,
            },
            raw_sha256=sha256(rawpath),
            boundary_status="Label-derived candidate boundary, not independent biological membrane ground truth",
            sha256={p.name: sha256(p) for p in out.glob("*.tif")},
        )
        atomic_json(out / "metadata.json", meta)
        return meta

    def start_export(self, payload):
        scope = payload.get("scope", "current")
        case = payload.get("case")
        target = payload.get("target")
        self.key(case, target)
        if scope not in ("current", "saved", "all"):
            raise ValueError("Invalid scope")
        with self.lock:
            pairs = (
                [(case, target, self.load(case, target))]
                if scope == "current"
                else [
                    (c, t, self.load(c, t))
                    for c in self.records
                    for t in self.targets
                    if (scope == "all" and t == target)
                    or (scope == "saved" and self.doc_path(c, t).exists())
                ]
            )
        if not pairs:
            raise ValueError("No saved annotations")
        if not self.export_lock.acquire(blocking=False):
            raise ValueError("An export is already running")
        ident = time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
        try:
            root = contained_path(self.root, "center_exports/" + ident)
            root.mkdir(parents=True, exist_ok=False)
        except Exception:
            self.export_lock.release()
            raise
        job = dict(id=ident, state="running", done=0, total=len(pairs), path=str(root))
        with self.jobs_lock:
            self.jobs[ident] = job

        def work():
            rows = []
            try:
                for c, t, d in pairs:
                    rows.append(self.export_pair(c, t, d, root))
                    with self.jobs_lock:
                        job["done"] += 1
                atomic_json(root / "manifest.json", {"records": rows, "schema": SCHEMA})
                atomic_json(root / "SUCCESS.json", {"pairs": len(rows), "complete": True})
                with self.jobs_lock:
                    job["state"] = "complete"
            except Exception as exc:
                with self.jobs_lock:
                    job.update(state="error", error=str(exc))
                atomic_json(root / "ERROR.json", dict(job))
            finally:
                self.export_lock.release()

        # Let an in-progress export finish if the local HTTP server is stopped normally.
        threading.Thread(target=work, daemon=False).start()
        return dict(job)
