from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image
from pptx import Presentation

from sdgen.analyze import analyze_deck
from sdgen.cli import main
from sdgen.content import Content, ImageValue, load_markdown, skeleton_markdown
from sdgen.inventory import inspect_deck
from sdgen.manifest import Binding, FieldSpec, GlobalSpec, Manifest, ShapeRef, SlideRules
from sdgen.registry import Registry
from sdgen.render import render

CARRIER_DECK = Path("D:/NTT-architectures/NTT DATA Inc I IT I Solution Design I Carrier AP Invoice Integration.pptx")


def _fixture_manifest(sample_deck) -> Manifest:
    analysis = analyze_deck(inspect_deck(sample_deck))
    for cand in analysis.candidates:
        if cand.kind == "image":
            cand.include = True
    manifest = analysis.to_manifest("demo", source="sample.pptx")
    manifest.globals.append(GlobalSpec(key="subject", replaces="Demo Integration"))
    manifest.slides.exclude = [2]
    return manifest


def _shape_text(slide, name):
    return next(s for s in slide.shapes if s.name == name).text_frame.text


def test_render_fills_all_kinds_and_excludes_slides(sample_deck, tmp_path):
    manifest = _fixture_manifest(sample_deck)
    image = tmp_path / "new.png"
    Image.new("RGB", (60, 30), "green").save(image)
    image_key = next(f.key for f in manifest.fields if f.kind == "image")
    content = Content(
        globals={"subject": "Carrier Invoices"},
        fields={
            "business_need": "Fresh need text\n- with a bullet",
            "scope": [{"Function": "Finance", "Countries": "ZA"}, {"Function": "Ops", "Countries": "KE"}],
            "first_point": "- alpha\n  - beta",
            image_key: ImageValue(path=str(image)),
        },
    )
    out = tmp_path / "out.pptx"
    result = render(sample_deck, manifest, content, out)

    assert result.slides == 1 and not result.errors
    slide = Presentation(str(out)).slides[0]
    assert slide.shapes.title.text == "Executive Overview: Carrier Invoices"
    assert _shape_text(slide, "Business Need Box") == "Business Need: Fresh need text\nwith a bullet"
    table = next(s for s in slide.shapes if s.has_table).table
    assert [[c.text for c in r.cells] for r in table.rows] == [["Function", "Countries"], ["Finance", "ZA"], ["Ops", "KE"]]
    body = slide.placeholders[1].text_frame
    assert [(p.text, p.level) for p in body.paragraphs] == [("alpha", 0), ("beta", 1)]
    picture = next(s for s in slide.shapes if hasattr(s, "image"))
    assert picture.image.size == (60, 30)


def test_missing_values_keep_blank_or_placeholder(sample_deck, tmp_path):
    manifest = _fixture_manifest(sample_deck)
    result = render(sample_deck, manifest, Content(), tmp_path / "kept.pptx", missing="keep")
    messages = [str(i) for i in result.issues]
    assert any("subject" in m and "left in place" in m for m in messages)
    assert any("business_need" in m for m in messages)
    kept = Presentation(str(tmp_path / "kept.pptx")).slides[0]
    assert _shape_text(kept, "Business Need Box").startswith("Business Need: Something long")

    render(sample_deck, manifest, Content(), tmp_path / "blank.pptx", missing="blank")
    blank = Presentation(str(tmp_path / "blank.pptx")).slides[0]
    assert _shape_text(blank, "Business Need Box") == "Business Need: "
    table = next(s for s in blank.shapes if s.has_table).table
    assert [[c.text for c in r.cells] for r in table.rows] == [["Function", "Countries"], ["", ""]]

    result = render(sample_deck, manifest, Content(), tmp_path / "placeholder.pptx")
    shown = Presentation(str(tmp_path / "placeholder.pptx")).slides[0]
    assert _shape_text(shown, "Business Need Box") == "Business Need: [To be completed: Business Need]"
    table = next(s for s in shown.shapes if s.has_table).table
    assert [[c.text for c in r.cells] for r in table.rows] == [["Function", "Countries"], ["[To be completed]", ""]]
    assert shown.placeholders[1].text_frame.text == "[To be completed: First point]"
    assert any("placeholder shown" in str(i) for i in result.issues)
    assert not result.errors


def test_overflow_continues_on_cloned_slides(sample_deck, tmp_path):
    manifest = _fixture_manifest(sample_deck)
    manifest.slides.exclude = []
    spec = manifest.field("first_point")
    spec.bindings[0].max_chars = 40
    lines = [f"- point number {i} with some words" for i in range(1, 7)]
    result = render(sample_deck, manifest, Content(fields={"first_point": "\n".join(lines)}), tmp_path / "long.pptx")

    prs = Presentation(str(tmp_path / "long.pptx"))
    titles = [s.shapes.title.text if s.shapes.title else None for s in prs.slides]
    extra = titles.count("Executive Overview: Demo Integration (cont.)")
    assert extra >= 2 and result.slides == 2 + extra
    assert titles[0] == "Executive Overview: Demo Integration"
    collected = [p.text for s in list(prs.slides)[: 1 + extra] for p in s.placeholders[1].text_frame.paragraphs]
    assert collected == [line[2:] for line in lines]
    assert any("continued on" in str(i) for i in result.issues)


def test_continuation_is_limited_to_listed_slides(sample_deck, tmp_path):
    manifest = _fixture_manifest(sample_deck)
    manifest.slides.exclude = []
    manifest.field("first_point").bindings[0].max_chars = 40
    content = Content(fields={"first_point": "\n".join(f"- point number {i} with some words" for i in range(1, 7))})

    limited = render(sample_deck, manifest, content, tmp_path / "limited.pptx", continue_on=[])
    assert limited.slides == 2
    assert any("about 40 fit" in str(i) for i in limited.issues)

    allowed = render(sample_deck, manifest, content, tmp_path / "allowed.pptx", continue_on=[1])
    assert allowed.slides > 2


def test_bad_bindings_produce_errors_not_crashes(sample_deck, tmp_path):
    manifest = Manifest(
        name="broken",
        fields=[
            FieldSpec(key="a", label="A", bindings=[Binding(slide=9, shape=ShapeRef(id=1))]),
            FieldSpec(key="b", label="B", bindings=[Binding(slide=1, shape=ShapeRef(id=999, name="Nope"))]),
            FieldSpec(key="c", label="C", kind="table", bindings=[Binding(slide=1, shape=ShapeRef(id=3, name="Business Need Box"))]),
        ],
        slides=SlideRules(exclude=[7]),
    )
    content = Content(fields={"a": "x", "b": "y", "c": [{"k": "v"}]})
    result = render(sample_deck, manifest, content, tmp_path / "broken.pptx")
    levels = {i.field: i.level for i in result.issues if i.field}
    assert levels == {"a": "error", "b": "error", "c": "error"}
    assert (tmp_path / "broken.pptx").exists()


def test_cli_skeleton_and_render_via_registry(sample_deck, tmp_path):
    registry_dir = tmp_path / "templates"
    Registry(registry_dir).add("demo", sample_deck, _fixture_manifest(sample_deck))
    runner = CliRunner()

    skeleton = runner.invoke(main, ["skeleton", "demo", "--templates", str(registry_dir), "-o", str(tmp_path / "c.md")])
    assert skeleton.exit_code == 0, skeleton.output
    text = (tmp_path / "c.md").read_text(encoding="utf-8")
    text = text.replace('subject: ""', "subject: Demo Renamed").replace("## business_need\n", "## business_need\nHello there\n")
    (tmp_path / "c.md").write_text(text, encoding="utf-8")

    out = tmp_path / "rendered.pptx"
    rendered = runner.invoke(main, ["render", "demo", str(tmp_path / "c.md"), "-o", str(out), "--templates", str(registry_dir)])
    assert rendered.exit_code == 0, rendered.output
    assert "Wrote" in rendered.output and "warning:" in rendered.output
    slide = Presentation(str(out)).slides[0]
    assert slide.shapes.title.text == "Executive Overview: Demo Renamed"
    assert _shape_text(slide, "Business Need Box") == "Business Need: Hello there"


@pytest.mark.skipif(not CARRIER_DECK.exists(), reason="example deck not available")
def test_carrier_deck_end_to_end(tmp_path):
    manifest = analyze_deck(inspect_deck(CARRIER_DECK)).to_manifest("ntt", source=CARRIER_DECK.name)
    markdown = skeleton_markdown(manifest)
    markdown = markdown.replace('subject: ""', "subject: Lockbox Integration")
    markdown = markdown.replace("## business_need\n", "## business_need\nBank statements arrive daily.\n- CAMT.053 files\n  - one per account\n")
    head, _, tail = markdown.partition("## scope\n")
    section, _, rest = tail.partition("\n## ")
    section = section.replace("|  |  |  |  |", "| Finance | AR | ZA | 3 banks |")
    markdown = head + "## scope\n" + section + "\n## " + rest
    content = load_markdown(markdown, manifest)
    out = tmp_path / "carrier-out.pptx"
    result = render(CARRIER_DECK, manifest, content, out)

    assert not result.errors, [str(i) for i in result.errors]
    deck = inspect_deck(out)
    assert len(deck.slides) == 30
    assert deck.slides[4].title == "Executive Overview: Lockbox Integration"
    need = next(s for s in deck.slides[4].walk() if s.text.startswith("Business Need: "))
    assert need.text == "Business Need: Bank statements arrive daily.\nCAMT.053 files\none per account"
    scope = next(s for s in deck.slides[4].walk() if s.kind == "table" and s.table.cells[0][0] == "Function")
    assert scope.table.cells == [["Function", "BU / Practice", "Operating Countries", "Carriers"], ["Finance", "AR", "ZA", "3 banks"]]
    assert not any("Carrier AP Invoice Integration" in s.title for s in deck.slides if s.title)


def test_field_modes_keep_and_blank_override_content(sample_deck, tmp_path):
    manifest = _fixture_manifest(sample_deck)
    content = Content(fields={"business_need": "Fresh need text", "first_point": "- alpha"})
    out = tmp_path / "modes.pptx"
    result = render(sample_deck, manifest, content, out, field_modes={"business_need": "keep", "scope": "blank", "first_point": "blank"})
    assert not result.errors
    assert any("template content kept" in str(i) for i in result.issues)
    slide = Presentation(str(out)).slides[0]
    assert _shape_text(slide, "Business Need Box").startswith("Business Need: Something long")
    table = next(s for s in slide.shapes if s.has_table).table
    assert [[c.text for c in r.cells] for r in table.rows] == [["Function", "Countries"], ["", ""]]
    body = slide.placeholders[1].text_frame.text
    assert "alpha" not in body and "To be completed" not in body


def test_hidden_and_reordered_slides_with_slide_map(sample_deck, tmp_path):
    manifest = _fixture_manifest(sample_deck)
    manifest.slides.exclude = []
    content = Content(fields={"business_need": "Need"})
    result = render(sample_deck, manifest, content, tmp_path / "order.pptx", order=[2, 1])
    assert result.slides == 2 and result.slide_map == [2, 1]
    prs = Presentation(str(tmp_path / "order.pptx"))
    assert prs.slides[0].shapes.title is None and prs.slides[1].shapes.title.text.startswith("Executive Overview")

    hidden = render(sample_deck, manifest, content, tmp_path / "hidden.pptx", hidden=[2])
    assert hidden.slides == 1 and hidden.slide_map == [1]

    manifest.field("first_point").bindings[0].max_chars = 40
    long = Content(fields={"first_point": "\n".join(f"- point number {i} with some words" for i in range(1, 7))})
    spread = render(sample_deck, manifest, long, tmp_path / "spread.pptx", order=[2, 1])
    assert spread.slide_map[0] == 2 and set(spread.slide_map[1:]) == {1} and len(spread.slide_map) >= 3


def test_titles_replace_slide_titles_keeping_the_subject(sample_deck, tmp_path):
    manifest = _fixture_manifest(sample_deck)
    out = tmp_path / "titles.pptx"
    result = render(sample_deck, manifest, Content(globals={"subject": "Carrier Invoices"}), out, titles={1: "Overview", 9: "Nothing"})
    assert not result.errors
    assert Presentation(str(out)).slides[0].shapes.title.text == "Overview: Carrier Invoices"
