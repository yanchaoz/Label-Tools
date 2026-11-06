# Center-Point Instance Refinement

The optional `centers` dependency adds SciPy and scikit-image. This mode is independent of the
polygon workspace: existing polygon commands and datasets remain unchanged. The center UI is
currently English, with configurable target names and an optional physical pixel size.

## Input and Processing

`centers-init` reads matching filename stems non-recursively. Every grayscale image needs an
aligned binary foreground mask (0/1 or 0/255). Optional nonnegative integer instance masks must
have exactly the same foreground. Images and masks must be 2D and at most 16 megapixels.
Missing masks are errors, not negative training examples. The importer refuses an existing output
directory and never edits input files. Raw dtype and pixel values are preserved in TIFF; display
windowing affects previews only. Pixel size is optional; unknown scale remains in pixels.

Within each original region, two or more centers drive a marker-controlled watershed using the
negative normalized distance transform and an optional image-gradient term. Distinct existing
instance IDs are never merged. Disconnected pieces of an old ID become separate instances.
Regions without centers are retained. Brush edits explicitly modify foreground; the instance
partition always covers exactly the edited foreground. Erased pixels lose their old instance identity:
repainting them does not resurrect old partitions. A fully erased, repainted connected region starts
as one instance unless multiple center points split it. Surviving neighboring IDs remain protected.
No neural model or SAM download is involved.

Points use original image `[x, y]` coordinates; arrays use `[y, x]`. Points outside the foreground or
duplicated at a marker pixel are flagged and block export until fixed. They are not snapped to an
object. Sparse uint32 source IDs are compacted internally for processing, then mapped back;
new split IDs use unused positive values. IDs are local to each image/target, not global identities.

This method cannot infer a missing object from a point alone, recover biologically correct boundaries
without sufficient image/mask evidence, or merge separate original IDs. Adjust the foreground and
review the output instead of treating the preview as validated ground truth.

## Workspace

```text
workspace/
  manifest.json           # schema: label-tools-centers-v1
  images/                 # display PNGs
  raw/                    # lossless source TIFFs
  foreground/             # imported binary masks
  instances/              # imported uint32 IDs
  center_annotations/     # authoritative current documents
  center_history/         # revision snapshots
  center_exports/         # timestamped export runs
```

The manifest records `workspace_id`, optional `resolution_nm`, `targets`, and `records` containing
`id`, `group`, `shape_yx`, `image`, `raw_tiff`, source checksums and target-specific `foreground`
and `instances` paths. The CLI creates one target; the schema supports multiple targets with shared
raw images. Optional per-target `strict_ignore`, `pseudo_ignore`, `source_ignore` and
`conflict_ignore` masks must be aligned. All paths stay inside the workspace, including symlinks.

A document contains `revision`, `points: [{id, x, y}]`, `strokes: [{mode, radius, points}]`,
`boundary_width`, `image_weight`, `reviewed`, and `notes`. Stroke mode is `add` or `erase`; radius
and boundary width are in pixels. Disk saves use a file lock and optimistic revisions. A stale
browser cannot overwrite a newer revision. Browser-local draft recovery is secondary to disk JSON.

## Export Semantics

The export dialog offers the current image/target, saved documents across targets, or all images
for the current target. Exports snapshot annotations and run as a local background job, with one
active job per server. They do not alter the source masks. A run is complete only when its `SUCCESS`
marker (`SUCCESS.json`) exists; failed runs retain `ERROR.json` and must not be consumed as complete datasets.

Each pair contains:

- `instances.tif`: uint32, 0 background, positive instance IDs.
- `foreground.tif`, `boundary.tif`, `interior.tif`: uint8 binary masks (0/1).
- `classes.tif`: uint8; 0 background, 1 interior, 2 derived boundary.
- `training_ignore_strict.tif`, `training_ignore_pseudo.tif`: uint8; 1 means ignore.
- `training_target_strict.tif`: the three-class target with strict-ignore pixels set to 255.
- Annotation JSON and provenance/checksums; raw TIFF is stored once per image in the run.

Center placement is weak supervision, not whole-image review. An unreviewed image without an
explicit strict-ignore mask is **entirely ignored for strict training**. Candidate boundaries and
brush-changed neighborhoods remain ignored while unreviewed. The pseudo-label path is separate
and must never be silently substituted for strict ground truth. After explicit full review, source
and conflict ignore masks, and whole border-touching instances, are still ignored. A reviewed flag
is an operator assertion, not an authenticated scientific validation.

Boundary targets are derived from the final instance partition with configurable pixel width,
clipped to foreground. They are not independent membrane annotations. A training adapter must
honor ignore values, preserve raw image scale, and enforce specimen-level train/validation splits.

## Local Service

The server binds only to `127.0.0.1`, checks Host and same-origin writes, uses a per-process token,
and serves bundled frontend assets without external requests. Do not expose it to an untrusted
network. Workspaces may include private source filenames: publish only synthetic examples, never
real data or exported annotations without separate authorization.
