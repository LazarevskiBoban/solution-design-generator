---
name: add-reference-pack
description: Add a new system's reference architectures as a pack (or extend an existing pack) so the planner can offer them and the drawings can follow that vendor's conventions. Use when a template or brief is about a system that has public reference architectures.
---

# Add a reference pack

A pack is one yaml file in `src/sdgen/packs/` plus the sources `sdgen refs --fetch` downloads
into `assets/refs/<system>/`. `src/sdgen/references.py` loads every yaml in the folder, so a
new system is a new file, no code. `assets/refs/README.md` documents the fields.

## Steps

1. **Find the source.** A public GitHub repository with one folder per architecture and the
   diagrams as `.drawio` (or another parseable format) works out of the box. Note the repository,
   branch, the folder holding the per-entry subfolders and the file names to keep.
2. **Write `src/sdgen/packs/<system>.yaml`** with `system`, `name`, `site` (page URL prefix that
   the entry `slug` completes), `repo`, `branch`, `root`, `keep`, `detect` and `entries`.
   - `detect` is a regex over the brief text; it gates the pack, so make it specific to the
     vendor's product names and acronyms and test it against a brief that must not match.
   - Each entry: `id` (the subfolder name), `title`, `slug`, `tags`, a one-line `summary` and
     `keywords` (phrases the ranking in `candidates()` looks for; keyword hits count double,
     so put the protocols, product names and scenario words a brief would use).
3. **Fetch and check**: `sdgen refs --system <system> --fetch`, then `sdgen refs` shows every
   entry with a fetched marker. `references.labels(system, id)` must return the block names of
   a fetched diagram; if the vendor's files are not draw.io, extend `_models`/`labels`.
4. **Conventions**: measure the vendor's style (containers, nodes, edges, fonts, logo, icon
   placement) on one or two sources and add a table to `assets/refs/README.md`. If the vendor
   needs its own accent colours, add tokens to `src/sdgen/palette.py` and a pack-aware switch
   in `flow.py` and `drawio.py`; today only the SAP accent exists.
5. **Tests** in `tests/test_references.py`: the pack loads with the expected number of entries,
   `matching()` turns it on for a matching brief and not for another, `candidates()` ranks the
   obvious entry first for two or three sample briefs, and a skip-if-absent check compares the
   palette tokens with the fetched sources.

## Extending an existing pack

Add the entry to the yaml (id, title, slug, tags, summary, keywords), run `sdgen refs --fetch`,
update the entry count in the tests. Keep summaries under about 120 characters; the whole
catalogue goes into the planner prompt.

## How the planner uses a pack

`plan_flows` calls `references.matching(brief text)`; for each pack it adds a `reference` enum
of qualified ids (`<system>:<id>`) to the schema, lists the catalogue in the prompt and, when
sources are fetched, adds the labels of the top `candidates()` so the model names blocks the
vendor's way. The chosen id is stored on the flow, shown as a link in the Diagrams step and
written as a footnote into the draw.io file.
