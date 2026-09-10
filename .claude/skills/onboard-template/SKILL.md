---
name: onboard-template
description: Register a new PowerPoint template with sdgen, understand what the analyzer proposed (sections, fields, image slots) and debug a section it missed. Use when a new deck template arrives or a template's outline looks wrong.
---

# Onboard a template

A template is a corporate deck registered once. The analyzer proposes fillable fields and
sections; the stored copy has each field replaced by a placeholder and each drawn diagram by an
image slot; a manifest and an outline describe it. Templates live under `templates/<name>/`
(or the folder `SDGEN_TEMPLATES` points at) and are git-ignored.

## Through the app

Templates page: upload the deck, review the proposed sections (title, kind, what to provide),
untick or rename, save. Removing a template also deletes the designs built on it.

## Through the CLI

```
sdgen inspect deck.pptx [--slide N]        # what is on the slides: shapes, tables, text
sdgen analyze deck.pptx [--all] [-o manifest.yaml]   # proposed fields; --all shows the unticked candidates
sdgen outline deck.pptx                    # proposed sections
sdgen add <name> deck.pptx [--manifest manifest.yaml] [--keep-content]
```

`add` tokenizes the deck (placeholders and image slots) unless `--keep-content` is given, and
derives the outline. A manifest YAML next to a deck can also be used directly by `render`.

## When a section is missing or wrong

1. `sdgen inspect deck.pptx --slide N` shows the shapes the analyzer saw: names, text, tables,
   pictures, groups. Sections are found from titles, headers and text boxes; drawn diagrams
   become one image slot covering their bounding box (`src/sdgen/diagrams.py`).
2. `sdgen analyze deck.pptx --all` lists the candidates it rejected and why.
3. Fix by confirming a different proposal in the manifest (kind, label, guidance, bindings) and
   re-adding with `--manifest`; do not special-case one customer's deck in code. The layout
   logic in `src/sdgen/layout.py` groups shapes into title band, footer band and content
   blocks; `src/sdgen/analyze.py` and `src/sdgen/blueprint.py` hold the proposals.
4. Run the analyzer and blueprint tests; the customer example deck is outside the repo and its
   tests skip when it is absent, so add a small synthetic deck to `tests/conftest.py` style
   fixtures for any new rule.

## After registering

Start a design from the template in the app, draw the diagrams from a brief and preview the
slides: the image slot size decides columns or rows for the flow drawing, so check the diagram
slides first.
