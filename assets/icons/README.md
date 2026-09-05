# Diagram icons

Flow diagrams use the SAP BTP Solution Diagram icons when the brief is about SAP.

1. Download the SVG folder of the SAP BTP Solution Diagrams repository
   (https://github.com/SAP/btp-solution-diagrams, folder
   `assets/shape-libraries-and-editable-presets/svg`, Apache-2.0).
2. Copy the files named in `src/sdgen/icons.yaml` into `assets/icons/sap/` next to this file
   (or point the `SDGEN_ICONS` environment variable at the folder that holds them).
3. Run `sdgen icons` once to build the PNG renditions that PowerPoint drawings use
   (needs PowerPoint on this machine). draw.io files use the SVGs directly.

Catalogue keys without a file draw as plain shapes; add a file name in `icons.yaml` to give
them an icon.
