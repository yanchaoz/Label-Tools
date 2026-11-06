# Architecture

```text
Input images -> dataset.py -> immutable raw TIFF + display PNG + manifest
                                   |
Browser canvas <-> server.py <-> storage.py <-> authoritative JSON revisions
                                   |                  |
                              geometry.py        reviewed export
                                   |                  |
                           uint16 labels + ignore masks + raw TIFF pairs
```

## Boundaries

- `dataset.py`: import, display-window metadata, portable paths, checksums, and manifest validation.
- `geometry.py`: geometry validation and XY-to-YX rasterization, independent of HTTP.
- `storage.py`: review state, per-workspace process/thread locking, revision conflicts, history and exports.
- `server.py`: loopback HTTP transport, same-origin writes, bounded JSON requests and static asset serving.
- `cli.py`: import/demo/verify/serve/export commands, with no hardcoded interpreter or data paths.
- `web/`: dependency-light Canvas UI, localized strings, local draft recovery and icon assets.

The API is intentionally small:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/dataset` | Manifest summary, review status, process token |
| `GET /api/case/{id}` | Authoritative current document or an unannotated draft |
| `POST /api/case/{id}` | Validate and save with revision comparison |
| `POST /api/export` | Build a reviewed-only training ZIP |
| `GET /exports/{name}` | Download a ZIP created by this workspace |

Write requests use JSON and `X-Annotation-Token`. The token is process-local, not a public authentication
system. A 409 signals a revision conflict; the client preserves its draft and requires explicit recovery.

## Center Workspace

The optional `centers/` package uses a separate manifest and document schema. `centers/dataset.py`
handles paired image/mask import, revisioned JSON, immutable export snapshots and TIFF provenance.
`centers/engine.py` owns foreground brush rasterization, region-constrained seeded watershed,
uint32 ID preservation and derived boundaries. `centers/server.py` reuses the polygon server's
response/security helpers with its own loopback listener and static frontend.

Center routes are `GET /api/dataset`, `/api/doc`, `/api/raw`, `/api/job` and `POST /api/save`,
`/api/preview`, `/api/export`. Preview is computed on demand; annotation JSON autosaves independently
and training TIFFs are written only by explicit export. See [the format contract](center-points.md).

## Extending

Add a source adapter by producing a validated manifest plus raw TIFF and preview PNG files. Keep image
pixels and annotation coordinates aligned, and record any resampling. Add a training adapter after the
reviewed ZIP boundary, where the accepted revision and ignore semantics are explicit.

Add geometry tools by producing the documented polygon schema and extending the backend validators
and rasterizer together. Do not bypass validation from the frontend or treat predicted masks as approved
human annotations. Multi-class targets, polygon holes, and automatic model predictions are future work,
not implied capabilities of this release.

## Implementation References

- [Python packaging metadata](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
- [Cross-platform file locking](https://py-filelock.readthedocs.io/en/latest/)
- [Python CI on GitHub Actions](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)
