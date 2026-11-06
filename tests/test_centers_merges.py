"""Point-selected unions must not alter foreground or unselected labels."""

import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("skimage")
from label_tools.centers.engine import refine, validate_doc


def fixture():
    labels = np.zeros((96, 128), dtype="uint32")
    labels[10:30, 10:30] = 3
    labels[10:30, 30:50] = 9
    labels[50:70, 70:90] = 12
    labels[10:20, 100:110] = 20
    doc = dict(points=[], strokes=[], merges=[], boundary_width=1, image_weight=0)
    return np.zeros(labels.shape, dtype="uint8"), labels > 0, labels, doc


def test_merge_removes_internal_boundary_only():
    raw, fg, labels, doc = fixture()
    before = refine(raw, fg, labels, doc)
    doc["merges"] = [dict(id=1, points=[[20, 20], [40, 20]])]
    after = refine(raw, fg, labels, doc)
    assert after["instance_count"] == 3
    assert np.array_equal(after["foreground"], fg)
    assert after["instances"][20, 20] == after["instances"][20, 40] == 3
    assert np.all(after["instances"][labels == 12] == 12)
    assert np.all(after["instances"][labels == 20] == 20)
    assert before["boundary"][20, 29] and not after["boundary"][20, 29]
    assert after["merge_info"][0]["merged_count"] == 1
    assert after["invalid_merges"] == []
    doc["merges"] = []
    assert np.array_equal(refine(raw, fg, labels, doc)["instances"], labels)


def test_disconnected_merge_never_paints_background():
    raw, fg, labels, doc = fixture()
    doc["merges"] = [dict(id=1, points=[[20, 20], [80, 60]])]
    result = refine(raw, fg, labels, doc)
    assert result["instance_count"] == 3
    assert np.array_equal(result["instances"] > 0, fg)
    assert not result["foreground"][40, 50]


def test_transitive_groups_and_background_atomic_rejection():
    raw, fg, labels, doc = fixture()
    doc["merges"] = [
        dict(id=1, points=[[20, 20], [40, 20]]),
        dict(id=2, points=[[40, 20], [80, 60]]),
        dict(id=3, points=[[80, 60], [105, 15], [0, 0]]),
    ]
    result = refine(raw, fg, labels, doc)
    assert result["invalid_merges"] == [3]
    assert result["instance_count"] == 2
    assert result["instances"][15, 105] == 20
    assert result["merge_info"][0]["instance_id"] == result["merge_info"][1]["instance_id"]


def test_merge_after_split_updates_point_mapping():
    raw, fg, labels, doc = fixture()
    doc["points"] = [dict(id=1, x=15, y=20), dict(id=2, x=25, y=20)]
    assert refine(raw, fg, labels, doc)["instance_count"] == 5
    doc["merges"] = [dict(id=1, points=[[15, 20], [25, 20]])]
    result = refine(raw, fg, labels, doc)
    assert result["instance_count"] == 4
    assert result["point_mapping"][0]["instance_id"] == result["point_mapping"][1]["instance_id"]


def test_same_instance_group_is_noop():
    raw, fg, labels, doc = fixture()
    doc["merges"] = [dict(id=1, points=[[15, 20], [20, 20]])]
    result = refine(raw, fg, labels, doc)
    assert np.array_equal(result["instances"], labels)
    assert result["merge_info"][0]["merged_count"] == 0


def test_sparse_uint32_merge():
    raw, fg, labels, doc = fixture()
    labels[labels == 9] = 4294967295
    doc["merges"] = [dict(id=1, points=[[20, 20], [40, 20]])]
    result = refine(raw, fg, labels, doc)
    assert result["instances"].dtype == np.uint32
    assert result["instance_count"] == 3


@pytest.mark.parametrize(
    "groups",
    [
        None,
        [dict(id=1, points=[[2, 2]])],
        [dict(id=True, points=[[2, 2], [3, 3]])],
        [dict(id=1, points=[[float("nan"), 2], [3, 3]])],
        [dict(id=1, points=[[128, 2], [3, 3]])],
        [dict(id=1, points=[[2, 2], [3, 3]])] * 2,
    ],
)
def test_invalid_merge_documents(groups):
    *_, doc = fixture()
    doc["merges"] = groups
    with pytest.raises(ValueError):
        validate_doc(doc, (96, 128))
