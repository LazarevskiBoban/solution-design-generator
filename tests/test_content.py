from pathlib import Path

from sdgen.content import (
    Content,
    ImageValue,
    dump_markdown,
    load_markdown,
    load_markdown_file,
    parse_pipe_table,
    skeleton_markdown,
    validate_content,
)
from sdgen.manifest import Binding, FieldSpec, GlobalSpec, Manifest, ShapeRef

SAMPLE = """---
subject: Demo Integration
version: 1.0
---

# Ignored document title

## business_need
Line one with **bold**.
- bullet a
  - nested b

## Scope
| Function | Countries |
|---|---|
| Finance | ZA, KE |
| Sales | first<br>second |

## diagram_image
![flow](images/flow.png)

## Something else
free text

```
## not a heading inside a fence
```
"""


def _manifest() -> Manifest:
    return Manifest(
        name="demo",
        globals=[GlobalSpec(key="subject", replaces="Old Subject")],
        fields=[
            FieldSpec(key="business_need", label="Business Need", bindings=[Binding(slide=1, shape=ShapeRef(id=3))]),
            FieldSpec(key="scope", label="Scope", kind="table", columns=["Function", "Countries"], bindings=[Binding(slide=1, shape=ShapeRef(id=4))]),
            FieldSpec(key="diagram_image", label="Diagram", kind="image", bindings=[Binding(slide=1, shape=ShapeRef(id=5))]),
        ],
    )


def test_load_with_manifest_matches_keys_and_labels(tmp_path):
    content = load_markdown(SAMPLE, _manifest(), base_dir=tmp_path)
    assert content.globals == {"subject": "Demo Integration", "version": "1.0"}
    assert content.fields["business_need"] == "Line one with **bold**.\n- bullet a\n  - nested b"
    assert content.fields["scope"] == [
        {"Function": "Finance", "Countries": "ZA, KE"},
        {"Function": "Sales", "Countries": "first\nsecond"},
    ]
    assert content.fields["diagram_image"] == ImageValue(path=str(tmp_path / "images" / "flow.png"))
    assert content.unknown == ["Something else"]


def test_details_headings_load_and_dump_next_to_their_field():
    text = (
        "## scope\n| Function | Countries |\n|---|---|\n| Finance | ZA |\n\n"
        "## business_need_details\nThe long version.\n\n"
        "## scope_details\n| Function | Countries |\n|---|---|\n| Finance | ZA |\n| Sales | KE |\n\n"
        "## business_need\nShort.\n"
    )
    content = load_markdown(text, _manifest())
    assert content.fields["business_need_details"] == "The long version."
    assert content.fields["scope_details"] == [{"Function": "Finance", "Countries": "ZA"}, {"Function": "Sales", "Countries": "KE"}]
    assert content.unknown == []
    dumped = dump_markdown(content, _manifest())
    assert [line for line in dumped.splitlines() if line.startswith("## ")] == ["## business_need", "## business_need_details", "## scope", "## scope_details"]
    assert "| Sales | KE |" in dumped


def test_load_without_manifest_guesses_kinds():
    content = load_markdown(SAMPLE)
    assert isinstance(content.fields["scope"], list)
    assert isinstance(content.fields["diagram_image"], ImageValue)
    assert content.fields["something_else"] == "free text\n\n```\n## not a heading inside a fence\n```"
    assert content.unknown == []


def test_dump_then_load_roundtrip():
    manifest = _manifest()
    content = Content(
        globals={"subject": "Demo"},
        fields={
            "business_need": "Para one\n- item",
            "scope": [{"Function": "Finance", "Countries": "ZA|KE"}, {"Function": "Ops", "Countries": "a\nb"}],
            "diagram_image": ImageValue(path="C:/img/flow.png"),
        },
    )
    text = dump_markdown(content, manifest)
    assert text.startswith("---\nsubject: Demo\n---")
    assert "| Finance | ZA\\|KE |" in text
    assert "![](C:/img/flow.png)" in text
    again = load_markdown(text, manifest)
    assert again.globals == content.globals
    assert again.fields == content.fields


def test_skeleton_lists_every_field_and_loads_empty():
    manifest = _manifest()
    text = skeleton_markdown(manifest)
    assert 'subject: ""' in text
    assert "## business_need" in text and "## scope" in text and "## diagram_image" in text
    assert "| Function | Countries |" in text
    content = load_markdown(text, manifest)
    assert content.unknown == []
    assert content.fields["business_need"] == ""
    assert content.fields["scope"] == [{"Function": "", "Countries": ""}]
    assert content.fields["diagram_image"] == ""


def test_validate_reports_gaps(tmp_path):
    manifest = _manifest()
    content = load_markdown(
        "## business_need\n\n## scope\n| Function | Region |\n|---|---|\n| x | y |\n\n## diagram_image\n![](missing.png)\n\n## bogus\nz\n",
        manifest,
        base_dir=tmp_path,
    )
    warnings = validate_content(content, manifest)
    assert any("global 'subject'" in w for w in warnings)
    assert any("'business_need'" in w and "empty" in w for w in warnings)
    assert any("columns not in the template: Region" in w for w in warnings)
    assert any("image not found" in w for w in warnings)
    assert any("section 'bogus'" in w for w in warnings)


def test_parse_pipe_table_edge_cases():
    assert parse_pipe_table("no table here") == []
    rows = parse_pipe_table("| A | B |\n|:---|---:|\n| 1 |\n| 2 | 3 | 4 |")
    assert rows == [{"A": "1", "B": ""}, {"A": "2", "B": "3"}]


def test_load_markdown_file_uses_its_folder(tmp_path):
    (tmp_path / "content.md").write_text("## pic\n![](a.png)\n", encoding="utf-8")
    content = load_markdown_file(tmp_path / "content.md")
    assert content.fields["pic"] == ImageValue(path=str(Path(tmp_path) / "a.png"))
