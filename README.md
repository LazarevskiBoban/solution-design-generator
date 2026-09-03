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

## Browser UI

```
sdgen ui
```

Templates page: upload a deck, review the proposed fields, save. The stored copy has every
confirmed section replaced by a `{{placeholder}}`; the manifest next to it describes the
fields. Generate page: pick the template, fill the fields (or import a Markdown content file),
generate, download.

Templates live under `templates/<name>/` (override with the `SDGEN_TEMPLATES` environment
variable).

## Commands

```
sdgen inspect deck.pptx            # slides, shapes, tables and text of a deck
sdgen inspect deck.pptx --json     # same as JSON
sdgen analyze deck.pptx            # propose fillable fields (add --all to see unticked ones)
sdgen analyze deck.pptx -o manifest.yaml --name my-template
sdgen add my-template deck.pptx --manifest manifest.yaml   # register (fields become placeholders)
sdgen skeleton my-template -o content.md      # empty content file for a registered template
sdgen render my-template content.md -o out.pptx
sdgen preview out.pptx --pdf out.pdf          # open in PowerPoint to verify, export PDF
pytest                             # run the test suite
```

`my-template` is a folder under `templates/` (change with `--templates`), or a path to a
manifest YAML that sits next to its deck.

## Content file

```
---
subject: Lockbox Integration
---

## business_need
One paragraph per line.
- bullets start with "- ", indent two spaces per level
- **bold** is supported

## scope
| Function | BU / Practice | Operating Countries | Carriers |
|---|---|---|---|
| Finance | AR | ZA | 3 banks |

## level2_flow_image
![](images/flow.png)
```
