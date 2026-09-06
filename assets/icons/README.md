# Diagram icons

Flow diagrams give every node an icon when the brief is about SAP: the SAP BTP service icons and
the generic SAP icons (SAP blue for SAP systems, grey for everything else) of the SAP BTP Solution
Diagrams repository (https://github.com/SAP/btp-solution-diagrams, Apache-2.0).

1. Run `sdgen icons --fetch` once. It downloads the files named in `src/sdgen/icons.yaml` into
   `assets/icons/sap/` next to this file (or into the folder the `SDGEN_ICONS` environment
   variable points at) and builds the PNG renditions that PowerPoint drawings use (needs
   PowerPoint on this machine). draw.io files use the SVGs directly.
2. Run `sdgen icons` again after editing the catalogue, `--fetch` when new files were added.

A key whose file is missing draws as a plain shape.
