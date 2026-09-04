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
    store.add_image(design, "flow_diagram", "flow.png", _png())
    store.add_image(design, "flow_diagram", "flow2.png", _png())
    store.add_image(design, "flow_diagram", "flow.png", _png())
    store.save(design)

    assert store.names() == ["camt-053"] and store.names("demo") == ["camt-053"] and store.names("other") == []
    loaded = store.load("camt-053")
    assert loaded.template == "demo" and loaded.brief.subject == "CAMT.053" and loaded.brief.about == "statements"
    assert loaded.content_markdown == "## business_need\nDraft text\n"
    assert loaded.images == {"flow_diagram": ["flow.png", "flow2.png"]}
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
