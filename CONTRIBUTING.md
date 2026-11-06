# Contributing

Use a feature branch and open a pull request. Keep datasets and derived outputs outside the checkout.
Do not change image values, coordinate conventions, review states, or ignore-mask semantics without
tests and an explicit format/version migration plan.

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests
python -m build
```

In a shared scientific Python environment, unrelated pytest plugins may run automatically. A clean
virtual environment is recommended. Alternatively, set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` for the test
process. The suite uses no third-party pytest plugins.

Frontend changes should be checked in a real browser: draw and edit a polygon, undo/redo, reload,
confirm an empty image, export reviewed pairs, and test an invalid contour. Verify desktop and narrow
layouts. Use synthetic images only for public screenshots and test fixtures.

Include the exact commands and results in your PR. Do not claim scientific accuracy from software
tests or synthetic examples. Please keep the English and Chinese interface strings synchronized.
