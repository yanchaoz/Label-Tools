"""Center refinement tests use generated geometry, never research image files."""

import json
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest
import tifffile

pytest.importorskip("scipy")
pytest.importorskip("skimage")
from label_tools.centers.dataset import CenterDataset, create_center_demo, import_centers
from label_tools.centers.engine import edge_ignore, refine, validate_doc
from label_tools.centers.server import CenterServer
from label_tools.cli import main
from label_tools.storage import RevisionConflict


def sample():
    y, x = np.mgrid[:96, :128]
    fg = ((x - 44) ** 2 + (y - 48) ** 2 < 25**2) | ((x - 82) ** 2 + (y - 48) ** 2 < 25**2)
    return (x + y).astype("uint8"), fg, fg.astype("uint32")


def doc(points=None):
    return dict(
        points=points or [],
        strokes=[],
        boundary_width=2,
        image_weight=0,
        reviewed=False,
        revision=0,
        notes="",
    )


def points():
    return [dict(id=1, x=44, y=48), dict(id=2, x=82, y=48)]


def test_split_partition_and_edges():
    raw, fg, labels = sample()
    r = refine(raw, fg, labels, doc(points()))
    assert r["instance_count"] == 2
    assert r["instances"][48, 44] != r["instances"][48, 82]
    assert np.array_equal(r["foreground"], fg)
    assert not (r["boundary"] & r["interior"]).any()
    assert np.array_equal(r["boundary"] | r["interior"], fg)
    assert r["boundary"][48, 62:66].any()


def test_existing_neighbors_never_merge():
    raw, fg, labels = sample()
    labels[:, 65:][fg[:, 65:]] = 9
    r = refine(raw, fg, labels, doc())
    assert np.array_equal(r["instances"], labels)


def test_sparse_max_uint32_ids_do_not_allocate_by_max_id():
    raw, fg, labels = sample()
    labels[fg] = np.iinfo("uint32").max
    r = refine(raw, fg, labels, doc(points()))
    assert r["instance_count"] == 2
    assert set(np.unique(r["instances"])) == {0, 1, 4294967295}


def test_disconnected_and_brush_edit():
    raw, fg, labels = sample()
    d = doc()
    d["strokes"] = [
        dict(mode="add", radius=3, points=[[8, 8], [15, 8]]),
        dict(mode="erase", radius=4, points=[[62, 0], [62, 95]]),
    ]
    r = refine(raw, fg, labels, d)
    assert r["foreground"][8, 8] and not r["foreground"][48, 62]
    assert r["instance_count"] == 3
    assert np.array_equal(r["instances"] > 0, r["foreground"])


def test_unseeded_objects_and_invalid_points():
    raw, fg, labels = sample()
    r = refine(raw, fg, labels, doc([dict(id=1, x=0, y=0)]))
    assert r["invalid_points"] == [1]
    assert np.array_equal(r["instances"], labels)
    r = refine(raw, fg, labels, doc([dict(id=1, x=44, y=48), dict(id=2, x=44, y=48)]))
    assert r["invalid_points"] == [2]


def test_edge_ignore_full_object():
    a = np.zeros((12, 12), "uint32")
    a[:4, 2:5] = 1
    a[6:8, 6:8] = 2
    assert np.array_equal(edge_ignore(a), a == 1)


@pytest.mark.parametrize(
    "p", [dict(id=1, x=float("nan"), y=2), dict(id=1, x=-1, y=2), dict(id=-1, x=2, y=2)]
)
def test_invalid_point(p):
    with pytest.raises(ValueError):
        validate_doc(doc([p]), (96, 128))


@pytest.fixture
def centers(tmp_path):
    images = tmp_path / "images"
    masks = tmp_path / "masks"
    images.mkdir()
    masks.mkdir()
    raw, fg, _ = sample()
    tifffile.imwrite(images / "test.tif", raw.astype("uint16") * 200)
    tifffile.imwrite(masks / "test.tif", fg.astype("uint8"))
    root = tmp_path / "workspace"
    import_centers(images, masks, root, resolution_nm=None)
    return CenterDataset(root)


def test_import_retains_raw_and_unknown_resolution(centers):
    record = centers.records["IMG0001"]
    raw, _, _ = sample()
    assert np.array_equal(
        tifffile.imread(centers.file(record["raw_tiff"])), raw.astype("uint16") * 200
    )
    assert centers.resolution is None


def test_revision_and_training_semantics(centers, tmp_path):
    d = doc(points())
    saved = centers.save("IMG0001", "object", d)
    with pytest.raises(RevisionConflict):
        centers.save("IMG0001", "object", d)
    out = tmp_path / "export"
    meta = centers.export_pair("IMG0001", "object", saved, out)
    assert meta["instance_count"] == 2 and not meta["human_reviewed"]
    assert np.all(tifffile.imread(out / "IMG0001/object/training_target_strict.tif") == 255)
    saved["reviewed"] = True
    saved = centers.save("IMG0001", "object", saved)
    centers.export_pair("IMG0001", "object", saved, tmp_path / "reviewed")
    assert (
        np.max(tifffile.imread(tmp_path / "reviewed/IMG0001/object/training_target_strict.tif"))
        == 2
    )
    assert list((centers.root / "center_history/IMG0001_object").glob("*.json"))


def test_invalid_points_block_export(centers, tmp_path):
    d = doc([dict(id=1, x=0, y=0)])
    with pytest.raises(ValueError):
        centers.export_pair("IMG0001", "object", d, tmp_path / "export")


def test_merge_save_reload_export(centers, tmp_path):
    d = doc(points())
    d["merges"] = [dict(id=1, points=[[44, 48], [82, 48]])]
    saved = centers.save("IMG0001", "object", d)
    reloaded = CenterDataset(centers.root).load("IMG0001", "object")
    assert reloaded["merges"] == saved["merges"]
    meta = centers.export_pair("IMG0001", "object", reloaded, tmp_path / "export")
    assert meta["instance_count"] == 1
    assert meta["merge_info"][0]["merged_count"] == 1


def test_invalid_merge_blocks_export(centers, tmp_path):
    d = doc()
    d["merges"] = [dict(id=1, points=[[44, 48], [0, 0]])]
    with pytest.raises(ValueError, match="invalid merge"):
        centers.export_pair("IMG0001", "object", d, tmp_path / "export")


def test_path_traversal_rejected(centers):
    m = centers.manifest
    m["records"][0]["image"] = "../outside.png"
    (centers.root / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(ValueError):
        CenterDataset(centers.root)


def test_missing_pair_is_not_negative(tmp_path):
    images = tmp_path / "images"
    masks = tmp_path / "masks"
    images.mkdir()
    masks.mkdir()
    tifffile.imwrite(images / "x.tif", np.ones((8, 8), "uint8"))
    with pytest.raises(ValueError):
        import_centers(images, masks, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_existing_output_not_overwritten(tmp_path):
    create_center_demo(tmp_path / "demo")
    with pytest.raises(FileExistsError):
        create_center_demo(tmp_path / "demo")


def test_cli_demo_and_invalid_port(tmp_path):
    assert main(["centers-demo", "--output", str(tmp_path / "demo")]) == 0
    assert len(CenterDataset(tmp_path / "demo").records) == 3
    with pytest.raises(SystemExit):
        main(["centers-serve", str(tmp_path / "demo"), "--port", "-1"])


def test_http_auth_preview_save_export(centers):
    srv = CenterServer(centers, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = srv.url

    def request(route, payload=None, token=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Annotation-Token"] = token
        if origin:
            headers["Origin"] = origin
        body = None if payload is None else json.dumps(payload).encode()
        return json.load(
            urllib.request.urlopen(urllib.request.Request(url + route, body, headers), timeout=10)
        )

    try:
        data = request("/api/dataset")
        assert len(data["records"]) == 1 and data["resolution_nm"] is None
        d = request("/api/doc?case=IMG0001&target=object")
        payload = dict(case="IMG0001", target="object", doc=d)
        with pytest.raises(urllib.error.HTTPError) as e:
            request("/api/save", payload)
        assert e.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as e:
            request("/api/save", payload, data["token"], "http://example.com")
        assert e.value.code == 403
        assert request("/api/save", payload, data["token"])["revision"] == 1
        with pytest.raises(urllib.error.HTTPError) as e:
            request("/api/save", payload, data["token"])
        assert e.value.code == 409
        assert request("/api/preview", payload, data["token"])["instance_count"] == 1
        job = request(
            "/api/export", dict(case="IMG0001", target="object", scope="current"), data["token"]
        )
        for _ in range(100):
            job = request("/api/job?id=" + job["id"])
            if job["state"] != "running":
                break
            time.sleep(0.05)
        assert job["state"] == "complete"
    finally:
        srv.shutdown()
        srv.server_close()
