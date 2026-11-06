"""Erasing discards old instance provenance; repainting does not resurrect it."""

import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("skimage")
from label_tools.centers.engine import refine  # noqa: E402


def test_repaint_forgets_old_ids_and_preserves_neighbors():
    raw = np.zeros((96, 128), dtype="uint8")
    labels = np.zeros_like(raw, dtype="uint32")
    labels[30:61, 30:50] = 7
    labels[30:61, 50:71] = 9
    labels[10:20, 100:110] = 13
    fg = labels > 0
    doc = dict(points=[], strokes=[], image_weight=0, boundary_width=1)
    assert refine(raw, fg, labels, doc)["instance_count"] == 3
    doc["strokes"] = [
        dict(mode="erase", radius=30, points=[[50, 45]]),
        dict(mode="add", radius=20, points=[[50, 45]]),
    ]
    result = refine(raw, fg, labels, doc)
    assert result["instance_count"] == 2
    assert result["instances"][45, 40] == result["instances"][45, 60]
    assert result["instances"][15, 105] == 13
    doc["points"] = [dict(id=1, x=40, y=45), dict(id=2, x=60, y=45)]
    assert refine(raw, fg, labels, doc)["instance_count"] == 3
    doc["points"] = []
    assert refine(raw, fg, labels, doc)["instance_count"] == 2
    doc["strokes"] = []
    assert np.array_equal(refine(raw, fg, labels, doc)["instances"], labels)
