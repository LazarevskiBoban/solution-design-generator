# Reference packs

A reference pack is one system's public reference architectures: a catalogue the planner offers to
the model when a brief is about that system, and the diagram sources behind it, fetched here so the
drawings can follow the vendor's conventions and vocabulary. The app stays generic: SAP is the first
pack, another vendor is another yaml file.

- Catalogue: `src/sdgen/packs/<system>.yaml` (committed). Fields: `system`, `name`, `site` (page
  URL prefix), `repo` and `branch` on GitHub, `root` (the repository folder with one subfolder per
  entry id), `keep` (file names to fetch from each entry folder), `detect` (a regex over the brief
  that turns the pack on) and `entries` with `id`, `title`, `slug`, `tags`, `summary` and
  `keywords` (phrases that pick the closest entries for a brief).
- Sources: `assets/refs/<system>/<id>/...` next to this file (or the folder `SDGEN_REFS` points
  at), ignored by git. Run `sdgen refs --fetch` once; `sdgen refs` lists what is fetched.
- Use: `references.matching(text)` gates a pack, `candidates(pack, text)` ranks its entries,
  `labels(system, id)` reads the block names off a fetched diagram, and the planner stores the
  chosen entry on the flow as `<system>:<id>`.

## SAP conventions

Measured on `sap/RA0032/drawio/sap-data-accelerator.drawio` and its Palantir variant, the same
tokens live in `src/sdgen/palette.py` and drive both the slide shapes and the draw.io export.

| Element | Style |
| --- | --- |
| SAP container | `rounded=1;arcSize=24;absoluteArcSize=1;strokeColor=#0070F2;strokeWidth=1.5;fillColor=#EBF8FF`, SAP logo (draw.io `img/lib/sap/SAP_Logo.svg`, 41x21) top-left, bold heading in `#002A86` |
| SAP node | same corners, `strokeColor=#0070F2;fillColor=#ffffff`, 160 or 200 x 55, Helvetica 12, label `<b>Name</b><br/><span style='font-size:10px;color:#595959'>subtitle</span>` |
| Non-SAP container | `strokeColor=#475E75;fillColor=#F5F6F7`, heading in slate |
| Non-SAP node | `fillColor=#F5F6F7;strokeColor=#475E75`, dotted border |
| Layer group inside a container | white, `dashed=1`, bold title top-left (not generated yet) |
| Edge | `edgeStyle=orthogonalEdgeStyle;strokeColor=#475E74;strokeWidth=1.5;endArrow=block;endFill=1`, `dashed=1` for the return or asynchronous path, 10 px label |
| Service icon | inside the node at the left edge, about 32 px, label to its right; icons come from the SAP BTP Solution Diagrams library (see `assets/icons/README.md`) |
| Layout | containers side by side (landscape, platform, partner side), nodes stacked, no per-kind colours or cylinders |

The published pictures carry a footer (SAP logo, "Architecture Center", date, hash, QR code) that
the website adds at build time; it is not part of the sources and not reproduced.
