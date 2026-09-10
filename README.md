# sdgen

Builds solution-design documents from a corporate PowerPoint template. A template is
uploaded once and understood as sections; each new design starts from a short brief, a few
diagram images and, where relevant, field mappings. A writer drafts the sections, the user
reviews them, and the deck (plus an Excel mapping workbook) is generated with the template's
layouts, fonts and styles intact.

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -e .[ui,llm,dev,preview]
sdgen ui
```

## Pages

- **Templates**: upload a deck once. The analyzer proposes the sections (title, kind, what to
  provide); confirm and save. The stored copy has every section replaced by a placeholder and
  every drawn diagram replaced by an image slot. Templates live under `templates/<name>/`.
- **New design**: pick a template, write the brief (what it is about, problem and outcome,
  approach, APIs and references, investigation notes, plus acceptance criteria, operations,
  non-functional facts, decisions and open questions for the developers) and answer the facts
  the template needs (dates, version, people, systems, counterparts, countries, targets, effort,
  investment, SAP objects); the app derives that list from the template's fields. Attach
  reference material: pictures of diagrams (transcribed once by the model into text you can
  correct, and shown to the model again when it draws a diagram), text files or pasted notes
  (Markdown, Mermaid, draw.io, CSV), and links (public pages are kept as text; paste an excerpt
  for pages behind a login), each tagged with the slides it is about. Open questions get their
  own slide in the draft. Upload
  diagram images or let the app draw the flows from the brief, draft the sections, review them,
  generate. Designs are saved under `designs/<name>/`; facts live in `brief.md` as
  `## fact:<key>` sections, reference material in `material.yaml` and `material/` next to it
  (`sdgen draft` reads them too). Drawn flows follow the conventions of the vendor's public reference
  architectures (SAP Architecture Center first, see `assets/refs/README.md`): tinted lane
  containers, uniform nodes with an icon and a subtitle, and a link to the closest reference
  architecture; the same drawing is kept as a draw.io file next to the design.
- **Mappings**: upload the target API definition (EDMX metadata, XSD or a sample payload) and
  one sample per source; map fields in a grid per source; download the workbook; push the
  summary into the brief.

## Model provider

The writer calls a provider chosen under **AI provider** in the sidebar (or by `SDGEN_LLM` on
the command line). `mock` (default) needs no key and echoes the brief into each section as a
labelled draft. `azure` and `openai` write real sections through the `openai` package
(`pip install -e .[llm]`).

For Azure AI Foundry open the project, **Keys and endpoints**, and take the Azure OpenAI
endpoint (`https://<resource>.openai.azure.com/`), one of the keys and the deployment name of
the model. Enter them in the sidebar for the session, or keep them in `.streamlit/secrets.toml`
(git-ignored) or environment variables:

```
AZURE_OPENAI_ENDPOINT = "https://<resource>.openai.azure.com/"
AZURE_OPENAI_API_KEY = "..."
AZURE_OPENAI_DEPLOYMENT = "gpt-5"
AZURE_OPENAI_API_VERSION = "2024-10-21"   # optional
```

Plain OpenAI uses `OPENAI_API_KEY` and, optionally, `SDGEN_OPENAI_MODEL`.

## Commands

```
sdgen inspect deck.pptx                   # slides, shapes, tables and text of a deck
sdgen analyze deck.pptx                   # proposed fields
sdgen outline deck.pptx                   # proposed sections
sdgen add my-template deck.pptx           # register (placeholders + image slots + outline)
sdgen brief -o brief.md                   # empty brief
sdgen draft my-template brief.md -o content.md [--llm mock|azure|openai] [--model gpt-5]
sdgen skeleton my-template -o content.md  # empty content file (manual route)
sdgen render my-template content.md -o out.pptx [--missing placeholder|keep|blank]
sdgen preview out.pptx --pdf out.pdf      # open in PowerPoint to verify, export PDF
sdgen mapping extract sample.xml          # fields in a sample or schema
sdgen mapping new camt --target metadata.xml --source "Bank A=a.xml" -o mappings.yaml
sdgen mapping workbook mappings.yaml -o mapping.xlsx
sdgen icons --fetch                       # diagram icons from the SAP BTP Solution Diagrams library
sdgen refs --fetch                        # reference architecture sources of every pack (assets/refs/)
pytest
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

## integration_architecture_diagram
![](images/flow.png)
```
