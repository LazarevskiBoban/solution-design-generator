---
name: run-app
description: Start, restart and drive the sdgen Streamlit app (the user's instance on port 8501 or a throwaway lab instance on 8511), check it is up, and screenshot a page or dialog. Use when asked to run or restart the app, or to see a change working in the real UI.
---

# Run the app

The UI is `ui/app.py`, a single Streamlit script over the `sdgen` package in `src/`. The
console script `sdgen ui` runs `streamlit run ui/app.py`; running Streamlit directly gives
control over the port and the environment.

## Start

```
.venv\Scripts\python.exe -m streamlit run ui\app.py --server.port 8501 --server.headless true
```

- Run it detached (PowerShell `Start-Process` with the output redirected to a log file, or the
  shell's background mode) so it outlives the command.
- Set `PYTHONIOENCODING=utf-8` when anything prints emoji or non-ASCII to a Windows console.
- Health check: `GET http://localhost:8501/_stcore/health` returns `ok`. The root page is only
  the Streamlit shell; the app itself renders over a websocket, so `curl` cannot see content.
- Open `http://localhost:8501` for the user once the health check passes.

## Restart after code changes

Streamlit reruns the script on save, but it does not reload the imported `sdgen` modules. After
editing anything under `src/sdgen/`, stop the process (find the listener with
`Get-NetTCPConnection -LocalPort 8501 -State Listen`) and start it again.

## A lab instance for experiments

Do not click around in the user's data. Start a second instance on port 8511 that points at
scratch copies of the registries:

```
SDGEN_TEMPLATES=<scratch>/templates  SDGEN_DESIGNS=<scratch>/designs  ... --server.port 8511
```

Copy a template folder and a design folder into the scratch location first. `SDGEN_LLM=mock`
keeps the writer offline; `SDGEN_ICONS` and `SDGEN_REFS` redirect the icon and reference folders.

## Drive it

- Pages and widgets: `streamlit.testing.v1.AppTest` works for everything except dialogs, file
  uploads included (`app.file_uploader(key=...).set_value([(name, bytes, mime)])`); see
  `tests/test_ui.py` for the fixture that points the registries at a temp folder. Note that
  AppTest lists an expander with an icon under `app.status`, and that expanders cannot be nested,
  so per-item editors inside a step use bordered containers.
- Dialogs and a real browser: Playwright with the Edge channel (`pip install playwright` in the
  venv, it is not a project dependency). Wait for `[data-testid='stAppViewContainer']`, then a
  short pause for the websocket render, then `page.screenshot`. Streamlit ignores a changed
  `expanded` value on an existing expander, which is why the step expanders take a key that
  changes with their open state.
- Look at the screenshot before reporting: a blank frame is a failed launch.

## What the pages are

Templates (register a deck), New design (brief, facts, diagrams, writing, review, generate),
Mappings (field mappings and the workbook). The Diagrams step draws flows from the brief and
keeps a draw.io and a Mermaid file per diagram; "Preview slides" exports the generated deck
through PowerPoint, so that button needs PowerPoint on the machine.
