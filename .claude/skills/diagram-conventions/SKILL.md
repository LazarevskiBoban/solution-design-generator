---
name: diagram-conventions
description: The look and rules of the flow diagrams sdgen draws (slide shapes in src/sdgen/flow.py, the draw.io export in src/sdgen/drawio.py, colours in src/sdgen/palette.py) and how to check a change visually. Use whenever those files change or a diagram looks wrong.
---

# Diagram conventions

The drawings follow the vendor's public reference architectures. SAP's Architecture Center is
the measured source; the style table lives in `assets/refs/README.md` and the colour tokens in
`src/sdgen/palette.py`. Change the tokens there, never inline colours in the renderers.

## The look

- **Neutral base plus a system accent.** Everything is slate strokes on light grey; SAP lanes and
  nodes switch to SAP blue on white or pale blue. `node_is_sap` (the icon decides, else the words
  of the label) and `lane_is_sap` (heading, else at least half the nodes) in `flow.py` make that
  call. A non-SAP design must render with no SAP colour or mark anywhere.
- **Lanes are containers**: rounded, tinted, heading top-left, white seam between them. On the
  slide SAP lanes carry a small bold "SAP" text mark; in draw.io they carry draw.io's built-in
  SAP logo (`img/lib/sap/SAP_Logo.svg`).
- **Nodes are uniform rounded boxes** (no cylinders, no per-kind colours): bold label, small grey
  subtitle, service icon inside at the left. Kind only informs the model's icon choice.
- **Edges**: orthogonal, slate 1.5 pt, block arrow head, dashed for async or file, small labels
  on white. Far edges travel on the seam between lanes, never across a tinted box.
- **Headings and subtitles come from the model** (`FlowSpec.lanes`, `FlowNode.subtitle`) with
  Source / Middleware / Target as the fallback; `FlowSpec.reference` names the closest
  reference architecture as `<system>:<id>`.

## Layout rules that tests pin

- Columns when the slot is at least 8 in wide or has one lane, rows otherwise; flat slots fall
  back to columns. Constants at the top of `flow.py`.
- Shape names are the contract with `render.py` and the tests (see the `test` skill).
- Everything stays inside the canvas the image slot gives; a far-edge channel is reserved at the
  bottom (columns) or the right (rows).

## Check a change visually

1. Slides: build a blank 13.333 x 7.5 in deck, call `draw_flow` with a spec that has subtitles,
   headings, an SAP lane and a non-SAP lane in a 12 x 5.5 in box and in a 6 x 5.5 in box, save,
   export PNGs with `sdgen.preview.export_slides` (PowerPoint COM) and look at them.
2. draw.io: write `to_drawio(spec)` to a file and render it in the diagrams.net viewer
   (`https://viewer.diagrams.net/#R` + the URL-encoded XML) through headless Edge, or open it in
   draw.io. Compare with `assets/refs/sap/RA0032/drawio/sap-data-accelerator.drawio`.
3. Look for: routes crossing a tint, labels on node borders, over-round corners, marks out of
   line with headings, anything SAP-coloured in a non-SAP spec.

## Known limits

Rows mode draws lane-to-lane elbow connectors bending horizontally first. Nested layer groups
inside a container (as in SAP's Palantir diagram) are not generated.
