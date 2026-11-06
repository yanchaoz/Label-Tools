"""Explicit point-selected label unions without changing the foreground."""

import numpy as np


def validate_merges(groups, shape):
    if not isinstance(groups, list) or len(groups) > 1000:
        raise ValueError("Invalid merge groups")
    h, w = shape
    clean, ids = [], set()
    total = 0
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("Expected a merge group object")
        ident = group.get("id")
        if type(ident) is not int or not 1 <= ident <= 1000000 or ident in ids:
            raise ValueError("Invalid merge group ID")
        ids.add(ident)
        points = group.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= 256:
            raise ValueError("A merge needs 2 to 256 points")
        total += len(points)
        if total > 20000:
            raise ValueError("Too many merge points")
        coordinates = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("Expected merge point [x, y]")
            x, y = map(float, point)
            if not np.isfinite([x, y]).all() or not (0 <= x < w and 0 <= y < h):
                raise ValueError("Merge point outside image")
            coordinates.append([x, y])
        clean.append(dict(id=ident, points=coordinates))
    return clean


def apply_merges(instances, groups):
    if not groups:
        return instances, [], [], {}
    parent, info, invalid = {}, [], []

    def root(label):
        while label in parent and parent[label] != label:
            parent[label] = parent.get(parent[label], parent[label])
            label = parent[label]
        return label

    # Resolve every point against the final split partition, never RGB overlay colors.
    for group in groups:
        selected = [int(instances[int(y), int(x)]) for x, y in group["points"]]
        if 0 in selected:
            invalid.append(group["id"])
            info.append(dict(id=group["id"], valid=False, reason="Point outside foreground"))
            continue
        roots = sorted({root(label) for label in selected})
        for label in roots:
            parent[label] = roots[0]
        info.append(
            dict(
                id=group["id"],
                valid=True,
                selected_ids=sorted(set(selected)),
                merged_count=len(roots) - 1,
                instance_id=roots[0],
            )
        )
    remap = {label: root(label) for label in parent}
    if any(label != value for label, value in remap.items()):
        values, inverse = np.unique(instances, return_inverse=True)
        mapped = np.array([remap.get(int(v), int(v)) for v in values], dtype="uint32")
        output = mapped[inverse].reshape(instances.shape)
    else:
        output = instances
    for row in info:
        if row["valid"]:
            row["instance_id"] = root(row["instance_id"])
    return output, info, invalid, remap
