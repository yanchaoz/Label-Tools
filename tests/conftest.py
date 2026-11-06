from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from label_tools.dataset import import_images
from label_tools.storage import Dataset


@pytest.fixture
def root(tmp_path: Path) -> Path:
    source = tmp_path / "input"
    source.mkdir()
    pixels = np.arange(64 * 80, dtype=np.uint16).reshape(64, 80).astype(np.uint8)
    for index in range(3):
        Image.fromarray(pixels).save(source / f"sample{index}.png")
    output = tmp_path / "workspace"
    import_images(source, output, resolution_nm=80, target="Test object")
    return output


@pytest.fixture
def dataset(root):
    return Dataset(root)


@pytest.fixture
def document():
    return {
        "revision": 0,
        "status": "complete",
        "notes": "Reviewed synthetic test",
        "polygons": [
            {"id": 1, "partial": False, "points": [[10, 12], [25, 12], [25, 30], [10, 30]]}
        ],
    }
