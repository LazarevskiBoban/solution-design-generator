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
- **Edges**: orthogonal, slate 1.5 pt, block arrow head, dashed for async, file or error, small
  labels on white. Routed edges travel on the seams and channels, never across a tinted box.
- **Headings and subtitles come from the model** (`FlowSpec.lanes`, `FlowNode.subtitle`) with
  Source / Middleware / Target as the fallback; `FlowSpec.reference` names the closest
  reference architecture as `<system>:<id>`.

## Layout rules that tests pin

- The geometry lives in `src/sdgen/flowlayout.py` as a pure function (`layout_flow`, EMU in,
  rectangles and point lists out); `flow.draw_flow` and `drawio.to_drawio` only paint what it
  returns, so both outputs place every box and line the same way.
- Default layout is **bands**: one horizontal band per lane, stacked in lane order, heading
  top-left inside the band. Each node gets a column from its rank along the edges (after its
  predecessors, unique inside its lane, edges that would close a cycle ignored), so a flow
  reads left to right across the bands. Nodes shrink to 1.1 in before the diagram wraps like a
  music score: every band is drawn again below and a routed line links the last node of a row,
  through the right channel, the corridor between the rows and the left channel, to the first
  node of the next row. A slot too flat for one band per lane falls back to columns.
- **Columns** (`FlowSpec.layout = "columns"`, the Diagrams step can switch a drawing) keeps the
  vertical lane containers with stacked nodes.
- Routes: neighbours in one band get a glued straight connector; a skip runs under the band in
  the seam; adjacent bands link through the seam between them; far bands use the right
  channel. Parallel edges between two nodes are drawn as offset lines with their labels stacked
  above (forward) and below (backward) the nodes, so no two labels overlap.
- Shape names are the contract with `render.py` and the tests: `<prefix> canvas`, `<prefix>
  lane <id>` (a second row adds ` row 2`), `... mark`, `<prefix> node <id>`, `<prefix> icon
  <id>`, `<prefix> edge <n>` and `... label`.
- Everything stays inside the canvas the image slot gives; channels are reserved only when a
  far edge or a second row needs them.

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

Cross-band edges are freeforms, not glued connectors, so they do not follow a node a reader
moves by hand. Many parallel edges between two nodes stack their labels into the band header.
Nested layer groups inside a container (as in SAP's Palantir diagram) are not generated.
