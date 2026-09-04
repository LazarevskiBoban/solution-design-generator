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


def _png() -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()
