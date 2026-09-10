---
name: test
description: Run the sdgen test suite the way the project expects, understand which tests skip and why, and write planner tests with a stub model. Use before committing, after touching src/sdgen or ui/app.py, or when a test result needs explaining.
---

# Test

```
.venv\Scripts\python.exe -m pytest -q
```

`pyproject.toml` sets `testpaths = ["tests"]`; there are no markers or plugins. A focused run
names the files: `... -m pytest -q tests/test_flow.py tests/test_drawio.py`.

## Skips and gates

- **PowerPoint COM tests** (`tests/test_icons.py`, `tests/test_preview.py`) run only with
  `SDGEN_COM_TESTS=1` on a machine that has PowerPoint. Everything else must stay green without it.
- **The example deck** (`tests/test_render.py`, `test_blueprint.py`, `test_analyze.py`,
  `test_diagrams.py`, `test_fill_slides.py`) is a customer file outside the repo; those tests skip
  when it is absent. Never copy it into the repo or hard-code its content in code.
- **Fetched assets**: `tests/test_references.py` compares the palette with the fetched SAP sources
  and skips until `sdgen refs --fetch` has run. Icon PNGs are never required by tests.

## Keeping the model out of tests

`tests/conftest.py` deletes the provider environment variables so a real key cannot leak in;
`SDGEN_LLM=mock` is the default. `MockLLM` returns no flows, so planner tests inject a small
stub with `name`, `complete` and `complete_json` that records the prompt and schema and returns a
fixed payload (see `_stub` in `tests/test_flow.py`). Assert on what the prompt contains and on
what survives `clean_flow`, not on model behaviour.

## Isolating folders

Tests that touch the registries set `SDGEN_TEMPLATES`, `SDGEN_DESIGNS`, `SDGEN_ICONS` or
`SDGEN_REFS` to a `tmp_path` through `monkeypatch`; `catalogue()` and `packs()` are cached, so
tests change folders, not the yaml files.

## Diagram drawing tests

`draw_flow` returns the shapes it created; tests read them by name (`"<prefix> canvas"`,
`"<prefix> lane <lane>"`, `"<prefix> lane <lane> mark"`, `"<prefix> lane <lane> title"`,
`"<prefix> node <id>"`, `"<prefix> icon <id>"`, `"<prefix> edge <n>"` and `... label`) and check
geometry: nodes inside their lane box, labels clear of nodes, routed edges outside the nodes they
pass. Keep those names when changing the renderer; `render.py` relies on the `Flow <key>` prefix too.

## Reading a result

A green run today is about 200 tests with a few skips (COM, and the example deck when it is
absent). Report the exact counts and paste failures verbatim; never re-run with fewer files to
make a failure disappear.
