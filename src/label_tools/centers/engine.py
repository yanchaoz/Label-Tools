"""Seed-constrained instance refinement; original labels are never modified."""

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from skimage.segmentation import find_boundaries, watershed


def validate_doc(doc, shape):
    h, w = shape
    if not isinstance(doc, dict):
        raise ValueError("Expected an annotation object")
    points = doc.get("points", [])
    strokes = doc.get("strokes", [])
    if not isinstance(points, list) or len(points) > 20000:
        raise ValueError("Too many points")
    if not isinstance(strokes, list) or len(strokes) > 10000:
        raise ValueError("Too many strokes")
    ids = set()

    def xy(p):
        x, y = map(float, p)
        if not np.isfinite([x, y]).all() or not (0 <= x < w and 0 <= y < h):
            raise ValueError("Point outside image")
        return [x, y]

    clean = []
    for p in points:
        i = p["id"]
        if not isinstance(i, int) or isinstance(i, bool) or i <= 0 or i > 1000000 or i in ids:
            raise ValueError("Invalid point ID")
        ids.add(i)
        x, y = xy([p["x"], p["y"]])
        clean.append(dict(id=i, x=x, y=y))
    brush = []
    for s in strokes:
        if s.get("mode") not in ("add", "erase"):
            raise ValueError("Invalid brush")
        radius = float(s["radius"])
        if not np.isfinite(radius) or not 1 <= radius <= 80:
            raise ValueError("Invalid brush radius")
        if not 1 <= len(s["points"]) <= 20000:
            raise ValueError("Invalid stroke length")
        brush.append(dict(mode=s["mode"], radius=radius, points=[xy(p) for p in s["points"]]))
    width = int(doc.get("boundary_width", 2))
    weight = float(doc.get("image_weight", 0.15))
    if not 1 <= width <= 8 or not np.isfinite(weight) or not 0 <= weight <= 1:
        raise ValueError("Invalid settings")
    return dict(
        points=clean,
        strokes=brush,
        boundary_width=width,
        image_weight=weight,
        reviewed=doc.get("reviewed") is True,
        notes=str(doc.get("notes", ""))[:4000],
    )


def brush_mask(foreground, strokes):
    im = Image.fromarray(foreground.astype("uint8"))
    draw = ImageDraw.Draw(im)
    for s in strokes:
        r = s["radius"]
        pts = [tuple(p) for p in s["points"]]
        value = int(s["mode"] == "add")
        if len(pts) > 1:
            draw.line(pts, fill=value, width=max(1, round(2 * r)), joint="curve")
        for x, y in pts:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=value)
    return np.asarray(im) > 0


def refine(raw, foreground, original, doc):
    if raw.ndim != 2 or raw.shape != foreground.shape or original.shape != foreground.shape:
        raise ValueError("Expected aligned 2D images and masks")
    if original.dtype.kind not in "ui" or original.min() < 0 or original.max() > 2**32 - 1:
        raise ValueError("Expected nonnegative uint32-compatible instance IDs")
    doc = validate_doc(doc, foreground.shape)
    fg = brush_mask(foreground, doc["strokes"])
    # Erasing invalidates old label provenance even if a later stroke repaints it.
    retained = brush_mask(original > 0, [s for s in doc["strokes"] if s["mode"] == "erase"])
    known = (original > 0) & fg & retained
    source_ids = np.unique(original[known])
    base = np.zeros(original.shape, dtype="uint32")
    base[known] = np.searchsorted(source_ids, original[known]).astype("uint32") + 1
    # Fill newly painted pixels within each foreground component, without merging old IDs.
    components, n = ndi.label(fg, structure=np.ones((3, 3)))
    next_id = int(base.max()) + 1
    for c, sl in enumerate(ndi.find_objects(components), 1):
        if sl is None:
            continue
        region = components[sl] == c
        local = base[sl]
        known = (local > 0) & region
        missing = region & (local == 0)
        if not missing.any():
            continue
        if known.any():
            indices = ndi.distance_transform_edt(
                ~known, return_distances=False, return_indices=True
            )
            local[missing] = local[tuple(indices[:, missing])]
        else:
            local[region] = next_id
            next_id += 1
    # Repair disconnected old IDs only; separate input instances are never unioned.
    for label, sl in enumerate(ndi.find_objects(base), 1):
        if sl is None:
            continue
        local = base[sl]
        cc, count = ndi.label(local == label, structure=np.ones((3, 3)))
        for j in range(2, count + 1):
            local[cc == j] = next_id
            next_id += 1
    grouped = {}
    invalid = []
    occupied = set()
    for p in doc["points"]:
        x, y = int(p["x"]), int(p["y"])
        label = int(base[y, x])
        if not label or (x, y) in occupied:
            invalid.append(p["id"])
            continue
        occupied.add((x, y))
        grouped.setdefault(label, []).append(p)
    mapping = []
    out = base.copy()
    boxes = ndi.find_objects(base)
    for label, points in grouped.items():
        sl = boxes[label - 1]
        region = base[sl] == label
        markers = np.zeros(region.shape, dtype="int32")
        for j, p in enumerate(points, 1):
            markers[int(p["y"]) - sl[0].start, int(p["x"]) - sl[1].start] = j
        distance = ndi.distance_transform_edt(region)
        surface = -distance / max(1.0, float(distance.max()))
        if doc["image_weight"]:
            grad = ndi.gaussian_gradient_magnitude(raw[sl].astype("float32") / 255, 1.0)
            surface += doc["image_weight"] * np.minimum(
                grad / max(0.001, float(np.quantile(grad[region], 0.95))), 1
            )
        split = watershed(
            surface, markers, mask=region, connectivity=np.ones((3, 3)), watershed_line=False
        )
        local = out[sl]
        for j, p in enumerate(points, 1):
            assigned = label if j == 1 else next_id
            if j > 1:
                next_id += 1
            local[split == j] = assigned
            mapping.append(dict(point_id=p["id"], instance_id=assigned, source_region=label))
    # Dense working labels bound memory even when inputs contain sparse uint32 IDs.
    table = np.zeros(int(out.max()) + 1, dtype="uint32")
    table[1 : len(source_ids) + 1] = source_ids
    occupied_ids = set(map(int, source_ids))
    candidate = 1
    for i in range(len(source_ids) + 1, len(table)):
        while candidate in occupied_ids:
            candidate += 1
        if candidate > 2**32 - 1:
            raise ValueError("Instance ID capacity exceeded")
        table[i] = candidate
        occupied_ids.add(candidate)
    out = table[out]
    for row in mapping:
        row["instance_id"] = int(table[row["instance_id"]])
        row["source_region"] = int(table[row["source_region"]])
    assert np.array_equal(out > 0, fg), "Instance partition changed the foreground"
    boundary = find_boundaries(out, connectivity=2, mode="inner") & fg
    if doc["boundary_width"] > 1:
        boundary = ndi.binary_dilation(boundary, iterations=doc["boundary_width"] - 1) & fg
    interior = fg & ~boundary
    classes = interior.astype("uint8") + 2 * boundary.astype("uint8")
    return dict(
        instances=out,
        foreground=fg,
        boundary=boundary,
        interior=interior,
        classes=classes,
        invalid_points=invalid,
        point_mapping=mapping,
        instance_count=int(np.unique(out[out > 0]).size),
    )


def edge_ignore(instances):
    ids = np.unique(
        np.concatenate([instances[0], instances[-1], instances[:, 0], instances[:, -1]])
    )
    return np.isin(instances, ids[ids > 0])


def rgba_labels(labels):
    ids = labels.astype("uint64")
    mixed = (ids * 2654435761) & 0xFFFFFFFF
    mixed = ((mixed ^ (mixed >> 16)) * 2246822519) & 0xFFFFFFFF
    mixed = mixed ^ (mixed >> 13)
    rgb = np.stack(
        [55 + ((mixed >> shift) & 255) * 190 // 255 for shift in (0, 8, 16)], axis=-1
    ).astype("uint8")
    return np.dstack([rgb, np.where(labels > 0, 180, 0).astype("uint8")])
