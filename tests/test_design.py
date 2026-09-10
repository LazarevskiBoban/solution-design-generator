import pytest
from PIL import Image

from sdgen.brief import Brief
from sdgen.content import ImageValue
from sdgen.design import Design, DesignStore
from sdgen.manifest import Binding, FieldSpec, Manifest, ShapeRef


def _manifest():
    return Manifest(
        name="demo",
        fields=[
            FieldSpec(key="business_need", label="Need", bindings=[Binding(slide=1, shape=ShapeRef(id=3))]),
            FieldSpec(key="flow_diagram", label="Flow", kind="image", bindings=[Binding(slide=2, shape=ShapeRef(id=4))]),
        ],
    )


def test_save_load_and_content(tmp_path):
    store = DesignStore(tmp_path / "designs")
    design = Design(name="CAMT 053", template="demo", brief=Brief(subject="CAMT.053", about="statements"))
    design.content_markdown = "## business_need\nDraft text\n"
    design.modes = {"executive_overview": "keep"}
    store.add_image(design, "flow_diagram", "flow.png", _png())
    store.add_image(design, "flow_diagram", "flow2.png", _png())
    store.add_image(design, "flow_diagram", "flow.png", _png())
    store.save(design)

    assert store.names() == ["camt-053"] and store.names("demo") == ["camt-053"] and store.names("other") == []
    loaded = store.load("camt-053")
    assert loaded.template == "demo" and loaded.brief.subject == "CAMT.053" and loaded.brief.about == "statements"
    assert loaded.content_markdown == "## business_need\nDraft text\n"
    assert loaded.images == {"flow_diagram": ["flow.png", "flow2.png"]}
    assert loaded.modes == {"executive_overview": "keep"}
    assert loaded.updated

    content = store.content(loaded, _manifest())
    assert content.globals == {"subject": "CAMT.053"}
    assert content.fields["business_need"] == "Draft text"
    images = content.fields["flow_diagram"]
    assert isinstance(images, list) and len(images) == 2 and all(isinstance(i, ImageValue) for i in images)


def test_mapping_set_is_persisted_with_the_design(tmp_path):
    from sdgen.mapping.model import FieldInfo, MappingEntry, MappingSet, SourceSpec, TargetSpec

    store = DesignStore(tmp_path / "designs")
    mapping = MappingSet(
        name="camt",
        target=TargetSpec(name="API", fields=[FieldInfo(path="A/B", required=True)]),
        sources=[SourceSpec(name="Bank A", fields=[FieldInfo(path="X/Y")])],
        entries=[MappingEntry(target_path="A/B", source="Bank A", source_path="X/Y")],
    )
    design = Design(name="camt", template="demo", mapping=mapping)
    store.save(design)
    assert (tmp_path / "designs" / "camt" / "mappings.yaml").is_file()
    loaded = store.load("camt")
    assert loaded.mapping == mapping
    assert loaded.workbook_name == "camt-mapping.xlsx"
    assert store.workbook_path(loaded).parent == tmp_path / "designs" / "camt" / "mapping"
    saved = store.add_mapping_file(loaded, "bank-a.xml", b"<x/>")
    assert saved.read_bytes() == b"<x/>"


def _png() -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def test_delete_removes_the_design_folder(tmp_path):
    store = DesignStore(tmp_path / "designs")
    design = Design(name="lockbox", template="demo", brief=Brief(subject="Lockbox"))
    store.save(design)
    store.add_image(design, "flow_diagram", "flow.png", b"png")
    assert store.names() == ["lockbox"]
    store.delete("lockbox")
    assert store.names() == [] and not (tmp_path / "designs" / "lockbox").exists()
    with pytest.raises(FileNotFoundError):
        store.delete("lockbox")


def test_templates_lists_the_owner_of_every_design(tmp_path):
    store = DesignStore(tmp_path / "designs")
    assert store.templates() == {} and store.names() == []
    store.save(Design(name="one", template="demo"))
    store.save(Design(name="two", template="gone"))
    assert store.templates() == {"one": "demo", "two": "gone"}
    assert store.names("demo") == ["one"] and store.names() == ["one", "two"]


def test_completed_steps_are_persisted(tmp_path):
    store = DesignStore(tmp_path / "designs")
    store.save(Design(name="d", template="demo", completed=["brief", "plan"]))
    assert store.load("d").completed == ["brief", "plan"]


def test_hidden_and_order_are_persisted(tmp_path):
    store = DesignStore(tmp_path / "designs")
    design = Design(name="lockbox", template="demo", hidden=["duplicate_checker"], order=["cover", "executive_overview"])
    store.save(design)
    loaded = store.load("lockbox")
    assert loaded.hidden == ["duplicate_checker"] and loaded.order == ["cover", "executive_overview"]


def test_flows_are_saved_next_to_the_design(tmp_path):
    from sdgen.flow import FlowEdge, FlowNode, FlowSpec

    store = DesignStore(tmp_path / "designs")
    design = Design(name="lockbox", template="demo")
    store.save(design)
    spec = FlowSpec(nodes=[FlowNode(id="a", label="A", lane="source"), FlowNode(id="b", label="B", lane="target")], edges=[FlowEdge(source="a", target="b")])
    store.save_flow(design, "level_2", spec)
    folder = tmp_path / "designs" / "lockbox" / "flows"
    assert (folder / "level_2.yaml").is_file() and (folder / "level_2.mmd").read_text(encoding="utf-8").startswith("flowchart LR")
    assert store.flows(design) == {"level_2": spec} and (folder / "level_2.drawio").is_file()
    store.delete_flow(design, "level_2")
    assert store.flows(design) == {} and not (folder / "level_2.mmd").exists() and not (folder / "level_2.drawio").exists()


def test_last_draft_is_persisted(tmp_path):
    store = DesignStore(tmp_path / "designs")
    design = Design(name="lockbox", template="demo", last_draft="## business_need\nModel text\n")
    store.save(design)
    assert (tmp_path / "designs" / "lockbox" / "draft.md").read_text(encoding="utf-8") == "## business_need\nModel text\n"
    assert store.load("lockbox").last_draft == design.last_draft


def test_material_is_persisted_and_files_removed(tmp_path):
    from sdgen.material import new_material

    store = DesignStore(tmp_path / "designs")
    design = Design(name="lockbox", template="demo", brief=Brief(subject="Lockbox"))
    picture = store.add_material(design, new_material("image", title="Landscape", tags=["flow"]), _png(), file_name="Landscape v1.png")
    note = store.add_material(design, new_material("text", title="Notes", text="reception, emission"))
    store.save(design)
    folder = tmp_path / "designs" / "lockbox"
    assert picture.file == f"{picture.id}-Landscape v1.png" and (folder / "material" / picture.file).is_file()
    assert (folder / "material.yaml").is_file() and "Landscape" not in (folder / "brief.md").read_text(encoding="utf-8")
    loaded = store.load("lockbox")
    assert loaded.brief.material == [picture, note]
    assert store.material_images(loaded, {"flow"}) == [(_png(), "image/png")] and store.material_images(loaded, {"other"}) == []
    store.remove_material(loaded, picture.id)
    store.save(loaded)
    assert not (folder / "material" / picture.file).exists() and [m.id for m in store.load("lockbox").brief.material] == [note.id]
    store.remove_material(loaded, note.id)
    store.save(loaded)
    assert not (folder / "material.yaml").exists() and store.load("lockbox").brief.material == []


def test_material_images_prefer_tagged_then_newest(tmp_path):
    from sdgen.material import new_material

    store = DesignStore(tmp_path / "designs")
    design = Design(name="d", template="demo")
    for title, tags, added in (("old", [], "2026-01-01T00:00:00+00:00"), ("new", [], "2026-02-01T00:00:00+00:00"), ("tagged", ["flow"], "2025-01-01T00:00:00+00:00"), ("other", ["x"], "2024-01-01T00:00:00+00:00")):
        store.add_material(design, new_material("image", title=title, tags=tags, added=added), title.encode(), file_name=f"{title}.png")
    store.add_material(design, new_material("text", title="note", text="t"))
    assert [d for d, _ in store.material_images(design, {"flow"})] == [b"tagged", b"new", b"old"]
    assert [d for d, _ in store.material_images(design, {"flow"}, limit=2)] == [b"tagged", b"new"]
    assert [d for d, _ in store.material_images(design)] == [b"tagged", b"other", b"new", b"old"]
    assert all(mime == "image/png" for _, mime in store.material_images(design))


def test_diagram_format_is_persisted(tmp_path):
    from sdgen.design import Design, DesignStore

    store = DesignStore(tmp_path / "designs")
    store.save(Design(name="d", template="t", diagram_format="drawio", diagram_formats={"level_2": "mermaid"}))
    loaded = store.load("d")
    assert loaded.diagram_format == "drawio" and loaded.diagram_formats == {"level_2": "mermaid"}
