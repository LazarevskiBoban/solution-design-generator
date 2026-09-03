# sdgen

Fills solution-design templates (PowerPoint first) from structured content while keeping the
template's layouts, fonts and styles. A template is uploaded once, its fillable sections are
confirmed and saved as a manifest; afterwards content is entered per document and rendered.

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -e .[ui,dev,preview]
```

## Commands

```
sdgen inspect deck.pptx            # slides, shapes, tables and text of a deck
sdgen inspect deck.pptx --json     # same as JSON
sdgen analyze deck.pptx            # propose fillable fields (add --all to see unticked ones)
sdgen analyze deck.pptx -o manifest.yaml --name my-template
pytest                             # run the test suite
```
