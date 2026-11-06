# Dataset and Export Format

This page describes polygon workspaces. Center-point workspaces use a separate schema and export
contract documented in [Center-Point Instance Refinement](center-points.md).

## Workspace

```text
workspace/
  manifest.json
  images/                 # uint8 browser previews, never used as raw training data
  images_tiff/            # lossless raw image pixels
  thumbnails/
  annotations/            # authoritative current JSON documents
  annotation_history/     # append-only revision snapshots
  labels/                 # derived uint16 masks; may include drafts
  ignore_masks/           # derived uint8 masks; 255 = partial target
  training_exports/       # reviewed-only ZIP files
```

Each image record contains `id`, `width`, `height`, `image`, `raw_tiff`, and optional
`resolution_nm`, `origin_xy`, `spatial_frame`, checksums, and display-window metadata.
IDs are safe ASCII filenames and unique ignoring case. Paths are relative to the workspace;
paths escaping it, including escaping symlinks, are rejected.

The importer records the source basename and checksum, not its absolute path. Do not publish
workspaces containing sensitive filenames or data. A completed import has `manifest.json` and
no `IMPORT_INCOMPLETE` marker. The original source directory is never edited.

## Geometry and Review

```json
{
  "id": "IMG0001",
  "revision": 3,
  "status": "complete",
  "notes": "Outer boundary checked",
  "polygons": [
    {"id": 1, "partial": false, "points": [[10, 12], [25, 12], [25, 30], [10, 30]]}
  ]
}
```

Points are `[x, y]` in original image pixels. Arrays are `[y, x]`. Coordinates must fall within
`0 <= x <= width-1` and `0 <= y <= height-1`. The polygon is closed implicitly; repeating the
first point is unnecessary. Rasterization uses Pillow's polygon fill, including rasterized boundary
pixels. Different instances may not share raster pixels, even when their continuous polygons only touch.
Self-crossings, nonfinite points, too-small contours, duplicate IDs, and overlapping areas are rejected.

`revision` is an optimistic lock: a save must carry the exact currently saved revision. The server
increments it on success. JSON is authoritative and committed after derived TIFFs. Exports re-rasterize
JSON so an interrupted mask write cannot turn a stale TIFF into training truth. A history record written
before an interrupted save may be orphaned; it is not the current accepted annotation.

States:

- `draft`: never included in reviewed training exports.
- `complete`: at least one instance; the operator confirms that all targets are accounted for.
- `negative`: explicitly confirmed empty, with zero instances.

A contour at or within one pixel of an image border is marked partial automatically. Partial regions
retain their instance IDs but also receive `ignore_mask=255`. Downstream code must honor that mask.
Do not reinterpret it as negative background or as a validated complete boundary.

Review states are operator assertions, not authenticated reviewer identities or independently validated
scientific truth. `annotation_method=manual_polygon` describes the representation/workflow, not proof
that a particular person created it. Automated API clients must not label predictions as reviewed.

## Physical Coordinates

`resolution_nm` is one positive scalar for square XY pixels; null means unknown. Anisotropic pixels
are not supported in this release. `origin_xy` is the crop origin in pixels at that record's scale.
`spatial_frame` names the shared image or section coordinate system. Overlap is tested only for matching
frames and resolutions with known origins. Missing metadata is **unknown**, not proof of no overlap.

Legacy manifests with `origin100` are accepted as a compatibility alias, without rewriting the source
manifest. New datasets should use `origin_xy` and explicit `resolution_nm`.

## Training ZIP

The ZIP is created atomically from reviewed JSON documents and contains:

- `images/{id}.tif`: raw image pixels and original dtype, not adjusted display previews.
- `labels/{id}.tif`: uint16 instance IDs, 0 for background, 1-65534 for instances within that image.
- `ignore_masks/{id}.tif`: uint8; 255 marks partial-instance regions.
- `annotations/{id}.json`: exact review state and accepted revision.
- `manifest.json`: dimensions, target, resolution, provenance, label paths, partial IDs, and overlap pairs.

Instance IDs are not globally unique across images. This is an explicit adapter format, not an nnU-Net,
COCO, or SAM training format. Implement and test the adapter required by your training system.
