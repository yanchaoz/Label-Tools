from concurrent.futures import ThreadPoolExecutor
import copy
import io
import json
import zipfile

import numpy as np
import pytest
import tifffile

from label_tools.storage import Dataset, RevisionConflict, spatial_overlap


def test_save_history_original_untouched(dataset, document):
    raw = dataset.root / dataset.records["IMG0001"]["raw_tiff"]
    before = raw.read_bytes()
    result = dataset.save("IMG0001", document)
    assert result["revision"] == 1 and result["resolution_nm"] == 80
    assert result["annotation_method"] == "manual_polygon"
    assert raw.read_bytes() == before
    assert len(list((dataset.root / "annotation_history").glob("*.json"))) == 1
    assert Dataset(dataset.root).load("IMG0001")["polygons"] == document["polygons"]


def test_stale_and_bool_revision_rejected(dataset, document):
    dataset.save("IMG0001", document)
    with pytest.raises(RevisionConflict):
        dataset.save("IMG0001", document)
    document["revision"] = True
    with pytest.raises(RevisionConflict):
        dataset.save("IMG0001", document)


def test_concurrent_dataset_objects_one_winner(dataset, document):
    other = Dataset(dataset.root)

    def save(data):
        try:
            data.save("IMG0001", copy.deepcopy(document))
            return "saved"
        except RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(save, [dataset, other]))
    assert sorted(results) == ["conflict", "saved"]
    assert dataset.load("IMG0001")["revision"] == 1


def test_only_reviewed_in_export_and_roundtrip(dataset, document):
    with pytest.raises(ValueError, match="No completed"):
        dataset.export()
    document["polygons"][0]["partial"] = True
    dataset.save("IMG0001", document)
    dataset.save("IMG0002", {"revision": 0, "status": "negative", "polygons": []})
    result = dataset.export()
    assert result["cases"] == 2
    with zipfile.ZipFile(result["path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["records"]) == 2
        assert all("IMG0003" not in name for name in archive.namelist())
        labels = tifffile.imread(io.BytesIO(archive.read("labels/IMG0001.tif")))
        ignore = tifffile.imread(io.BytesIO(archive.read("ignore_masks/IMG0001.tif")))
        assert labels.dtype == np.uint16 and set(np.unique(labels)) == {0, 1}
        assert np.all(ignore[labels == 1] == 255)
        assert not tifffile.imread(io.BytesIO(archive.read("labels/IMG0002.tif"))).any()
        assert (
            archive.read("images/IMG0001.tif")
            == (dataset.root / "images_tiff/IMG0001.tif").read_bytes()
        )
        assert all(record["image"] in archive.namelist() for record in manifest["records"])


def test_export_uses_authoritative_json_not_derived_mask(dataset, document):
    dataset.save("IMG0001", document)
    tifffile.imwrite(dataset.root / "labels/IMG0001.tif", np.zeros((64, 80), np.uint16))
    with zipfile.ZipFile(dataset.export()["path"]) as archive:
        labels = tifffile.imread(io.BytesIO(archive.read("labels/IMG0001.tif")))
        assert labels.max() == 1


def test_failed_save_keeps_authoritative_revision(dataset, document, monkeypatch):
    dataset.save("IMG0001", document)
    document["revision"] = 1

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tifffile, "imwrite", fail)
    with pytest.raises(OSError):
        dataset.save("IMG0001", document)
    assert dataset.load("IMG0001")["revision"] == 1


def test_invalid_edit_keeps_previous_annotation(dataset, document):
    dataset.save("IMG0001", document)
    document["revision"] = 1
    document["polygons"][0]["points"][0] = [-20, 10]
    with pytest.raises(ValueError):
        dataset.save("IMG0001", document)
    assert dataset.load("IMG0001")["revision"] == 1


def test_unknown_ids_do_not_create_files(dataset, document):
    with pytest.raises(KeyError):
        dataset.save("../outside", document)


def test_overlap_only_known_matching_frames():
    base = {"width": 50, "height": 50, "resolution_nm": 100, "spatial_frame": "section-A"}
    records = [
        dict(base, id="a", origin_xy=[0, 0]),
        dict(base, id="b", origin_xy=[20, 20]),
        dict(base, id="c", origin_xy=[50, 0]),
        dict(base, id="d"),
        dict(base, id="e", origin_xy=[0, 0], spatial_frame="section-B"),
    ]
    assert spatial_overlap(records) == [["b", "a"], ["c", "b"]]
