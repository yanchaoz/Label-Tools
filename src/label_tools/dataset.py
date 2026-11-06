"""Portable dataset import and manifest checks. Source pixels are never overwritten."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

import numpy as np
from PIL import Image, ImageDraw
import tifffile

MAX_PIXELS = 16777216
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def contained_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Dataset files must use relative paths")
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Dataset path escapes its root")
    return result


def checked_resolution(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("resolution_nm must be a finite positive number or null")
    return value


def load_manifest(root: Path) -> dict:
    value = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ValueError("Invalid dataset manifest")
    if value.get("schema_version", 1) != 1:
        raise ValueError("Unsupported dataset schema version")
    for field in ("dataset", "target"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"Manifest requires a nonempty {field}")
    resolution = checked_resolution(value.get("resolution_nm"))
    if not value["records"]:
        raise ValueError("Dataset has no images")
    ids = set()
    for record in value["records"]:
        if not isinstance(record, dict):
            raise ValueError("Invalid record")
        ident = record.get("id")
        if not isinstance(ident, str) or not ID_PATTERN.fullmatch(ident) or ident.casefold() in ids:
            raise ValueError("IDs must be safe, unique filenames (case-insensitive)")
        ids.add(ident.casefold())
        for axis in ("width", "height"):
            if type(record.get(axis)) is not int or record[axis] < 2:
                raise ValueError("Image dimensions must be positive integers >= 2")
        if record["width"] * record["height"] > MAX_PIXELS:
            raise ValueError("Image exceeds the 16-megapixel crop limit")
        for field in ("image", "raw_tiff"):
            if not contained_path(root, record.get(field)).is_file():
                raise ValueError(f"Missing image: {ident}/{field}")
        record["resolution_nm"] = checked_resolution(record.get("resolution_nm", resolution))
        # Accept earlier workspace manifests without rewriting their source data.
        if "origin100" in record and "origin_xy" not in record:
            record["origin_xy"] = record["origin100"]
            record["spatial_frame"] = value.get("source", value["dataset"])
        origin = record.get("origin_xy")
        if origin is not None:
            if (
                not isinstance(origin, list)
                or len(origin) != 2
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in origin)
            ):
                raise ValueError("origin_xy must be two finite numbers")
        frame = record.get("spatial_frame")
        if frame is not None and (not isinstance(frame, str) or not frame):
            raise ValueError("spatial_frame must be a nonempty string")
    value.setdefault("workspace_id", sha256(root / "manifest.json")[:32])
    value.setdefault("language", "en")
    return value


def read_image(path: Path) -> np.ndarray:
    if path.suffix.lower() in (".tif", ".tiff"):
        with tifffile.TiffFile(path) as tif:
            if len(tif.series) != 1 or len(tif.series[0].pages) != 1:
                raise ValueError(f"{path.name}: stacks and multi-series TIFFs are not supported")
            series = tif.series[0]
            if len(series.shape) not in (2, 3) or np.prod(series.shape[:2]) > MAX_PIXELS:
                raise ValueError(f"{path.name}: expected a 2D crop of at most 16 megapixels")
            array = series.asarray()
    else:
        with Image.open(path) as image:
            if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                raise ValueError(f"{path.name}: oversized or multi-frame image")
            if image.mode in ("P", "RGBA", "LA"):
                raise ValueError(
                    f"{path.name}: convert palette/alpha images to RGB or grayscale first"
                )
            array = np.asarray(image).copy()
    if array.ndim == 3 and (array.shape[-1] != 3 or array.dtype != np.uint8):
        raise ValueError(f"{path.name}: color images must be interleaved uint8 RGB")
    if array.ndim not in (2, 3) or min(array.shape[:2]) < 2 or array.dtype.kind not in "buif":
        raise ValueError(f"{path.name}: unsupported pixel format")
    if not np.isfinite(array).all():
        raise ValueError(f"{path.name}: NaN/Inf pixels are not supported")
    return array


def display_image(array: np.ndarray) -> tuple[np.ndarray, dict]:
    if array.dtype == np.uint8:
        return array, {"method": "identity"}
    low, high = (float(v) for v in np.percentile(array, [0.5, 99.5]))
    if high <= low:
        low, high = float(array.min()), float(array.max())
    if high <= low:
        return np.full(array.shape, 128, dtype=np.uint8), {"method": "constant", "value": low}
    display = np.clip((array.astype(np.float64) - low) / (high - low) * 255, 0, 255)
    return display.astype(np.uint8), {"method": "linear_percentile", "low": low, "high": high}


def import_images(
    source: Path, output: Path, *, resolution_nm=None, target="Object", language="en"
):
    checked_resolution(resolution_nm)
    source, output = source.resolve(), output.resolve()
    paths = sorted(
        (p for p in source.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda path: path.name.casefold(),
    )
    if not paths:
        raise ValueError("No supported images found in the input folder (non-recursive)")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Target cannot be empty")
    if language not in ("en", "zh"):
        raise ValueError("Language must be en or zh")
    output.mkdir(parents=True, exist_ok=False)
    marker = output / "IMPORT_INCOMPLETE"
    marker.write_text("Import incomplete; this folder is not an annotation workspace.\n")
    for folder in ("images", "images_tiff", "thumbnails"):
        (output / folder).mkdir()
    records = []
    for index, path in enumerate(paths, 1):
        raw = read_image(path)
        display, window = display_image(raw)
        ident = f"IMG{index:04d}"
        png = output / "images" / f"{ident}.png"
        tiff = output / "images_tiff" / f"{ident}.tif"
        Image.fromarray(display).save(png)
        thumbnail = Image.fromarray(display)
        thumbnail.thumbnail((192, 192))
        thumbnail.convert("RGB").save(output / "thumbnails" / f"{ident}.jpg", quality=88)
        axes = "YX" if raw.ndim == 2 else "YXS"
        tifffile.imwrite(
            tiff,
            raw,
            photometric="minisblack" if raw.ndim == 2 else "rgb",
            compression="deflate",
            metadata={"axes": axes, "resolution_nm": resolution_nm},
        )
        records.append(
            {
                "id": ident,
                "width": raw.shape[1],
                "height": raw.shape[0],
                "image": f"images/{ident}.png",
                "raw_tiff": f"images_tiff/{ident}.tif",
                "source_filename": path.name,
                "source_sha256": sha256(path),
                "raw_sha256": sha256(tiff),
                "preview_sha256": sha256(png),
                "raw_dtype": str(raw.dtype),
                "display_window": window,
                "resolution_nm": resolution_nm,
            }
        )
    manifest = {
        "schema_version": 1,
        "workspace_id": uuid.uuid4().hex,
        "dataset": output.name,
        "target": target,
        "resolution_nm": resolution_nm,
        "language": language,
        "records": records,
    }
    atomic_json(output / "manifest.json", manifest)
    marker.unlink()
    return manifest


def create_demo(output: Path, language="en") -> dict:
    """Generate only synthetic shapes, never patient or research images."""
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp)
        rng = np.random.default_rng(42)
        for i in range(3):
            pixels = np.clip(rng.normal(190, 7, (768, 768)), 0, 255).astype(np.uint8)
            image = Image.fromarray(pixels)
            draw = ImageDraw.Draw(image)
            draw.text((28, 25), "SYNTHETIC EXAMPLE - NOT RESEARCH DATA", fill=30)
            if i < 2:
                draw.ellipse((130 + i * 80, 170, 470 + i * 60, 540), fill=90, outline=35, width=6)
                draw.ellipse((205 + i * 80, 245, 390 + i * 60, 455), fill=150, outline=65, width=3)
            if i == 0:
                draw.ellipse((535, 465, 705, 675), fill=115, outline=45, width=5)
            image.save(source / f"synthetic_{i + 1}.png")
        return import_images(
            source, output, resolution_nm=100, target="Synthetic object", language=language
        )
