import json

import numpy as np
from PIL import Image
import pytest
import tifffile

from label_tools.dataset import create_demo, display_image, import_images, load_manifest, read_image
from label_tools.storage import Dataset


def test_import_verify_and_draft_defaults(dataset):
    assert dataset.verify()["images"] == 3
    assert all(r["annotation_status"] == "draft" for r in dataset.summary()["records"])
    assert dataset.records["IMG0001"]["resolution_nm"] == 80
    assert not list((dataset.root / "labels").iterdir())


def test_uint16_preserved_not_display_normalized(tmp_path):
    source = tmp_path / "in"
    source.mkdir()
    raw = np.arange(32 * 48, dtype=np.uint16).reshape(32, 48) * 20
    tifffile.imwrite(source / "highdepth.tif", raw)
    output = tmp_path / "out"
    manifest = import_images(source, output)
    record = manifest["records"][0]
    assert np.array_equal(tifffile.imread(output / record["raw_tiff"]), raw)
    assert np.asarray(Image.open(output / record["image"])).dtype == np.uint8
    assert record["display_window"]["method"] == "linear_percentile"
    assert record["resolution_nm"] is None


def test_rgb_preserved(tmp_path):
    source = tmp_path / "in"
    source.mkdir()
    pixels = np.zeros((30, 50, 3), dtype=np.uint8)
    pixels[:, :, 0] = 155
    Image.fromarray(pixels).save(source / "rgb.png")
    output = tmp_path / "out"
    record = import_images(source, output)["records"][0]
    assert np.array_equal(tifffile.imread(output / record["raw_tiff"]), pixels)


def test_constant_highdepth_preview():
    display, window = display_image(np.full((16, 16), 4000, dtype=np.uint16))
    assert np.all(display == 128) and window["method"] == "constant"


def test_nonfinite_and_stacks_rejected(tmp_path):
    nan = tmp_path / "nan.tif"
    tifffile.imwrite(nan, np.full((16, 16), np.nan, dtype=np.float32))
    with pytest.raises(ValueError, match="NaN"):
        read_image(nan)
    stack = tmp_path / "stack.tif"
    tifffile.imwrite(stack, np.zeros((5, 16, 16), np.uint8), photometric="minisblack")
    with pytest.raises(ValueError):
        read_image(stack)


@pytest.mark.parametrize("resolution", [-1, 0, float("nan"), float("inf"), True])
def test_bad_resolution_leaves_no_output(tmp_path, resolution):
    with pytest.raises(ValueError):
        import_images(tmp_path, tmp_path / "out", resolution_nm=resolution)
    assert not (tmp_path / "out").exists()


def test_existing_output_never_overwritten(root):
    original = (root / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        import_images(root / "images", root)
    assert (root / "manifest.json").read_bytes() == original


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "../escape"},
        {"image": "../../secret.png"},
        {"width": True},
        {"width": 10000000},
        {"origin_xy": [0, float("nan")]},
    ],
)
def test_bad_manifest(root, patch):
    path = root / "manifest.json"
    value = json.loads(path.read_text())
    value["records"][0].update(patch)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_manifest(root)


def test_case_insensitive_id_collision(root):
    path = root / "manifest.json"
    value = json.loads(path.read_text())
    value["records"][1]["id"] = value["records"][0]["id"].lower()
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="unique"):
        load_manifest(root)


def test_checksum_tamper_detected(dataset):
    raw = dataset.root / dataset.records["IMG0001"]["raw_tiff"]
    tifffile.imwrite(raw, np.zeros((64, 80), dtype=np.uint8))
    with pytest.raises(ValueError, match="checksum"):
        dataset.verify()


def test_synthetic_demo(tmp_path):
    root = tmp_path / "demo"
    create_demo(root, language="zh")
    data = Dataset(root)
    assert data.verify()["images"] == 3
    assert data.manifest["language"] == "zh"
