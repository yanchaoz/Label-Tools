# Verification

## Local Checks (2026-11-06)

- 70 Python unit/HTTP tests passed on Windows with Python 3.10.
- Tests cover XY/YX raster alignment, uint16 output, multiple IDs, partial ignore masks, invalid
  coordinates, self-intersections, raster overlap, review state, stale revisions, cross-process locks,
  interrupted writes, original-pixel preservation, raw checksum failures and HTTP request checks.
- A wheel was built and installed into an isolated test target; frontend files are package data.
- JavaScript syntax and Python lint checks passed.

## Real Browser Checks

Only generated synthetic images were used. The existing research annotation service and its images
were not modified for this framework test.

- Drew two independent polygons and edited a vertex; undo/redo and disk save worked.
- Confirmed the complete image, refreshed, and recovered the saved polygons and review status.
- Confirmed an empty image and exported it alongside the complete image; a draft was excluded.
- Deliberately drew a self-crossing polygon. Saving was rejected with an actionable error; undo
  restored an empty draft, which remained excluded from the reviewed export.
- Switched the interface between English and Chinese.
- Inspected desktop 1280 x 720 and narrow 390 x 844 layouts; fit-to-view preserved the image aspect ratio.
- Saved the public preview from the synthetic dataset, with no private images or local paths visible.

## CI and Limits

The repository workflow tests Windows, Linux, and macOS and builds the package. The CI status on the
pull request is the source of truth for each revision; the local result does not imply all remote jobs
have already passed. Browser interaction tests are manual, not part of the automated CI suite.

Not validated: network filesystems, public deployment, multiple authenticated users, arbitrary WSI sizes,
3D data, biological annotation accuracy, or downstream model performance. Synthetic software tests do
not establish scientific validity or specimen-level generalization.
