---
name: add-icons
description: Extend the diagram icon catalogue (src/sdgen/icons.yaml) with new SAP service or generic icons, fetch the files and build the PNG renditions. Use when a system named in briefs or reference diagrams draws without an icon.
---

# Add icons

The catalogue is `src/sdgen/icons.yaml`: a key the model assigns to a node, a `label` the model
reads, and a `file` from the SAP BTP Solution Diagrams repository. `assets/icons/README.md`
explains the folders. Icons are SAP-only today; a per-system icon pack would follow the
reference-pack pattern.

## Steps

1. **Find the file.** Service icons are `<number>-<name>_sd.svg` in the repository's
   `assets/shape-libraries-and-editable-presets/svg/` folder (list it through the GitHub tree API
   and grep). Generic glyphs come from the draw.io library in the same repository and are named
   `generic-<slug>-sap.svg` (SAP blue) or `generic-<slug>-nonsap.svg` (grey) by `library_svgs`.
   File names with spaces are not fetched safely; prefer another file.
2. **Add the entry** in the matching section of the yaml (SAP systems, SAP BTP services,
   non-SAP systems). Keys are short snake_case; labels say what the model should match
   ("SAP Task Center (central inbox)"). `Icon.sap` is derived from the file name, so an SAP-blue
   generic file or a service icon counts as SAP and colours the node accordingly.
3. **Fetch and build**: `sdgen icons --fetch` downloads the files into `assets/icons/sap/` and
   builds 256 px PNGs through PowerPoint (needs PowerPoint; draw.io uses the SVGs directly).
4. **Tests**: `tests/test_icons.py` checks every file name follows the two conventions; extend
   `test_sap_icons_are_told_apart_by_file_name` if the new key changes the SAP rule.
5. **Prompt size**: every key and label goes into the planner prompt when the brief is about
   SAP. Add what diagrams actually need, and merge near-duplicates.

## Finding gaps

Scan the fetched reference diagrams for product names without a key: read the labels of the
integration architectures with `references.labels` (or `_models` for all of them), keep texts
starting with "SAP ", and compare with the catalogue labels. That is how the current list of
BTP services was extended.
