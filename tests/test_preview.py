import os
from pathlib import Path

import pytest

from sdgen.preview import preview

POWERPOINT = Path("C:/Program Files/Microsoft Office/root/Office16/POWERPNT.EXE")


@pytest.mark.skipif(
    not POWERPOINT.exists() or os.environ.get("SDGEN_COM_TESTS") != "1",
    reason="set SDGEN_COM_TESTS=1 on a machine with PowerPoint",
)
def test_preview_opens_deck_and_exports_pdf(sample_deck, tmp_path):
    result = preview(sample_deck, tmp_path / "sample.pdf")
    assert result.opened, result.message
    assert result.slides == 2
    assert Path(result.pdf).is_file()


def test_preview_reports_unreadable_file(tmp_path):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a deck")
    result = preview(bad)
    assert not result.opened
    assert result.message


@pytest.mark.skipif(
    not POWERPOINT.exists() or os.environ.get("SDGEN_COM_TESTS") != "1",
    reason="set SDGEN_COM_TESTS=1 on a machine with PowerPoint",
)
def test_export_slide_images_writes_one_png_per_slide(sample_deck, tmp_path):
    from sdgen.preview import export_slide_images

    files = export_slide_images(sample_deck, tmp_path / "png", width=640)
    assert [f.name for f in files] == ["slide-01.png", "slide-02.png"]
    assert all(f.stat().st_size > 0 for f in files)


def test_export_slide_images_raises_on_unreadable_file(tmp_path):
    from sdgen.preview import export_slide_images

    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a deck")
    with pytest.raises(RuntimeError):
        export_slide_images(bad, tmp_path / "png")


def test_preview_rows_describe_each_field(sample_deck):
    from sdgen.analyze import analyze_deck
    from sdgen.blueprint import derive_blueprint
    from sdgen.content import Content
    from sdgen.inventory import inspect_deck
    from sdgen.preview import preview_rows

    deck = inspect_deck(sample_deck)
    analysis = analyze_deck(deck)
    manifest = analysis.to_manifest("demo")
    blueprint = derive_blueprint(deck, analysis, manifest, "demo")
    content = Content(fields={"business_need": "Fresh need text", "scope": [{"Function": "Finance", "Countries": "ZA"}]})
    rows = preview_rows(blueprint, manifest, content, {})
    by_field = {row["field"]: row for row in rows}
    assert by_field["Business Need"]["content"] == "Fresh need text" and by_field["Business Need"]["slide"] == 1
    assert by_field["Scope"]["content"] == "1 rows"
    assert by_field["First point"]["content"].startswith("(empty")
    blank = preview_rows(blueprint, manifest, content, {blueprint.sections[0].key: "blank"})
    assert all(row["content"] == "(blank)" for row in blank if row["slide"] == 1)
