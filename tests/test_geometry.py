import copy

import numpy as np
import pytest

from label_tools.geometry import rasterize, validate_document

RECORD = {"width": 80, "height": 64}


def test_xy_coordinates_not_transposed(document):
    labels, ignore = rasterize(validate_document(document, RECORD), RECORD)
    assert labels.dtype == np.uint16
    assert labels.shape == (64, 80)
    assert labels[12, 10] == 1 and labels[10, 12] == 0
    assert (labels == 1).sum() == 16 * 19
    assert not ignore.any()


@pytest.mark.parametrize(
    "points",
    [
        [[10, 10], [30, 30], [10, 30], [30, 10]],
        [[-1, 12], [25, 12], [25, 30]],
        [[80, 12], [25, 12], [25, 30]],
        [[10, 64], [25, 12], [25, 30]],
        [[float("nan"), 12], [25, 12], [25, 30]],
        [[float("inf"), 12], [25, 12], [25, 30]],
        [[10, 12], [25, 12]],
        [[10, 12], [10, 12], [10, 12]],
        [1, 2, 3],
        "invalid",
    ],
)
def test_invalid_points_rejected(document, points):
    document["polygons"][0]["points"] = points
    with pytest.raises(ValueError):
        validate_document(document, RECORD)


@pytest.mark.parametrize("ident", [0, -1, True, 65535, 1.5, "1"])
def test_invalid_instance_ids(document, ident):
    document["polygons"][0]["id"] = ident
    with pytest.raises(ValueError):
        validate_document(document, RECORD)


def test_duplicate_ids(document):
    document["polygons"].append(copy.deepcopy(document["polygons"][0]))
    with pytest.raises(ValueError):
        validate_document(document, RECORD)


def test_overlap_rejected(document):
    document["polygons"].append({"id": 2, "points": [[20, 20], [40, 20], [40, 40], [20, 40]]})
    with pytest.raises(ValueError, match="overlap"):
        validate_document(document, RECORD)


def test_shared_raster_edge_rejected(document):
    document["polygons"].append({"id": 2, "points": [[25, 12], [40, 12], [40, 30], [25, 30]]})
    checked = validate_document(document, RECORD)
    with pytest.raises(ValueError, match="share raster"):
        rasterize(checked, RECORD)


def test_multiple_ids_and_partial_ignore(document):
    document["polygons"][0]["partial"] = True
    document["polygons"].append({"id": 9, "points": [[40, 40], [55, 40], [55, 55], [40, 55]]})
    labels, ignore = rasterize(validate_document(document, RECORD), RECORD)
    assert set(np.unique(labels)) == {0, 1, 9}
    assert np.all(ignore[labels == 1] == 255)
    assert np.all(ignore[labels == 9] == 0)


def test_border_touch_automatically_partial(document):
    document["polygons"][0]["points"][0][0] = 0
    assert validate_document(document, RECORD)["polygons"][0]["partial"]


@pytest.mark.parametrize(
    "value", [None, [], {"status": "unknown"}, {"notes": 100}, {"polygons": [None]}]
)
def test_malformed_document(value):
    with pytest.raises(ValueError):
        validate_document(value, RECORD)


def test_no_empty_completed_or_nonempty_negative(document):
    document["status"] = "negative"
    with pytest.raises(ValueError):
        validate_document(document, RECORD)
    document.update(status="complete", polygons=[])
    with pytest.raises(ValueError):
        validate_document(document, RECORD)
    document["status"] = "negative"
    assert validate_document(document, RECORD)["status"] == "negative"
