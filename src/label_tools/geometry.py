"""Validate instance geometry independently of the browser and rasterizer."""

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon
from shapely.validation import explain_validity

MAX_INSTANCES = 500
MAX_VERTICES = 20000


def validate_document(value: dict, record: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Annotation must be a JSON object")
    status = value.get("status", "draft")
    if status not in ("draft", "complete", "negative"):
        raise ValueError("Unknown annotation status")
    objects = value.get("polygons", [])
    if not isinstance(objects, list) or len(objects) > MAX_INSTANCES:
        raise ValueError("Invalid polygon list")
    ids, geometries, cleaned = set(), [], []
    for obj in objects:
        if not isinstance(obj, dict):
            raise ValueError("Each instance must be an object")
        ident = obj.get("id")
        if type(ident) is not int or not 1 <= ident <= 65534 or ident in ids:
            raise ValueError("Instance IDs must be distinct integers from 1 to 65534")
        partial = obj.get("partial", False)
        if type(partial) is not bool:
            raise ValueError("partial must be a boolean")
        try:
            points = np.asarray(obj.get("points"), dtype=float)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Instance {ident}: invalid coordinates") from exc
        if points.ndim != 2 or points.shape[1] != 2 or not 3 <= len(points) <= MAX_VERTICES:
            raise ValueError(f"Instance {ident}: 3 to {MAX_VERTICES} vertices required")
        if (
            not np.isfinite(points).all()
            or (points < 0).any()
            or (points[:, 0] > record["width"] - 1).any()
            or (points[:, 1] > record["height"] - 1).any()
        ):
            raise ValueError(f"Instance {ident}: coordinates outside image")
        polygon = Polygon(points)
        if not polygon.is_valid or polygon.area < 4:
            raise ValueError(f"Instance {ident}: invalid contour ({explain_validity(polygon)})")
        if any(polygon.intersection(other).area > 0 for other in geometries):
            raise ValueError("Different instances overlap; correct their boundaries")
        touches = bool(
            (points[:, 0] <= 1).any()
            or (points[:, 1] <= 1).any()
            or (points[:, 0] >= record["width"] - 2).any()
            or (points[:, 1] >= record["height"] - 2).any()
        )
        ids.add(ident)
        geometries.append(polygon)
        cleaned.append({"id": ident, "points": points.tolist(), "partial": partial or touches})
    if status == "negative" and cleaned:
        raise ValueError("A confirmed negative must have no instances")
    if status == "complete" and not cleaned:
        raise ValueError("Empty field: explicitly confirm it as negative")
    notes = value.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 10000:
        raise ValueError("Notes must be text, at most 10000 characters")
    return {"status": status, "polygons": cleaned, "notes": notes}


def rasterize(document: dict, record: dict) -> tuple[np.ndarray, np.ndarray]:
    """Rasterize in image XY coordinates; arrays are YX, with no transposition."""
    shape = record["height"], record["width"]
    labels = np.zeros(shape, dtype=np.uint16)
    ignore = np.zeros(shape, dtype=np.uint8)
    for obj in document["polygons"]:
        mask = Image.new("1", (shape[1], shape[0]))
        ImageDraw.Draw(mask).polygon([tuple(point) for point in obj["points"]], fill=1)
        occupied = np.asarray(mask, dtype=bool)
        if np.any(occupied & (labels != 0)):
            raise ValueError("Contours share raster pixels; leave at least one pixel between them")
        labels[occupied] = obj["id"]
        if obj["partial"]:
            ignore[occupied] = 255
    return labels, ignore
