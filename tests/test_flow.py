from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.util import Inches

from sdgen.brief import Brief
from sdgen.flow import FlowEdge, FlowNode, FlowSpec, clean_flow, draw_flow, plan_flows, to_mermaid
from sdgen.llm import MockLLM

SPEC = FlowSpec(
    title="Lockbox transport",
    nodes=[
        FlowNode(id="bank", label="Bank (SWIFT FileAct)", kind="external", lane="source"),
        FlowNode(id="autoclient", label="AutoClient VM", kind="system", lane="source"),
        FlowNode(id="sftp", label="Azure Blob SFTP", kind="store", lane="middleware"),
        FlowNode(id="btp", label="SAP BTP Cloud Integration", kind="system", lane="middleware"),
        FlowNode(id="s4", label="S/4HANA lockbox processing", kind="system", lane="target"),
    ],
    edges=[
        FlowEdge(source="bank", target="autoclient", label="lockbox file", kind="file"),
        FlowEdge(source="autoclient", target="sftp", label="SFTP put", kind="file"),
        FlowEdge(source="sftp", target="btp", label="poll", kind="async"),
        FlowEdge(source="btp", target="s4", label="file drop", kind="sync"),
    ],
)


def _blank_slide():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    return prs, prs.slides.add_slide(prs.slide_layouts[6])


def test_draw_flow_in_columns_glues_connectors_and_stays_in_the_box():
    prs, slide = _blank_slide()
    box = (Inches(0.5), Inches(1.0), Inches(12.0), Inches(5.5))
    created = draw_flow(slide, box, SPEC, prefix="Flow x")
    names = [s.name for s in created]
    assert names[0] == "Flow x canvas" and created[0].width == box[2]
    assert sum(n.startswith("Flow x lane") for n in names) == 3
    assert sum(n.startswith("Flow x node") for n in names) == 5
    connectors = [s for s in created if s.name.startswith("Flow x edge") and not s.name.endswith("label")]
    labels = [s for s in created if s.name.endswith("label")]
    assert len(connectors) == 4 and len(labels) == 4 and labels[0].text_frame.text == "lockbox file"
    for connector in connectors:
        element = connector._element
        assert element.find(".//" + qn("a:stCxn")) is not None and element.find(".//" + qn("a:endCxn")) is not None
        assert element.find(".//" + qn("a:tailEnd")).get("type") == "triangle"
    left, top, width, height = box
    for shape in created:
        assert shape.left >= left - 1 and shape.top >= top - Inches(0.35)
        assert shape.left + shape.width <= left + width + Inches(0.75) and shape.top + shape.height <= top + height + 1
    bank = next(s for s in created if s.name == "Flow x node bank")
    s4 = next(s for s in created if s.name == "Flow x node s4")
    assert bank.left < s4.left and bank.text_frame.text == "Bank (SWIFT FileAct)"


def test_draw_flow_uses_rows_when_the_box_is_narrow():
    prs, slide = _blank_slide()
    created = draw_flow(slide, (Inches(0.5), Inches(1.0), Inches(6.0), Inches(5.5)), SPEC)
    bank = next(s for s in created if s.name.endswith("node bank"))
    sftp = next(s for s in created if s.name.endswith("node sftp"))
    s4 = next(s for s in created if s.name.endswith("node s4"))
    assert bank.top < sftp.top < s4.top
    assert bank.left + bank.width <= Inches(6.5) + 1


def test_mermaid_and_yaml_roundtrip(tmp_path):
    text = to_mermaid(SPEC)
    assert text.startswith("flowchart LR\n  subgraph source[Source]")
    assert '    bank{{"Bank (SWIFT FileAct)"}}' in text and '    sftp[("Azure Blob SFTP")]' in text
    assert "  bank ==>|lockbox file| autoclient" in text and "  sftp -.->|poll| btp" in text and "  btp -->|file drop| s4" in text
    SPEC.save(tmp_path / "flow.yaml")
    assert FlowSpec.load(tmp_path / "flow.yaml") == SPEC


def test_plan_flows_keeps_known_sections_and_cleans_edges():
    class JsonLLM:
        name = "j"

        def complete(self, system, user):
            return ""

        def complete_json(self, system, user, schema, name="result"):
            assert name == "flows" and "level_2 | Level 2 | end to end" in user
            return {
                "flows": [
                    {"section": "level_2", "title": "T", "nodes": [{"id": "a b", "label": "A", "lane": "source"}, {"id": "c", "label": "C", "lane": "target"}, {"id": "c", "label": "dup", "lane": "target"}], "edges": [{"source": "a b", "target": "c", "label": "x"}, {"source": "c", "target": "zzz"}]},
                    {"section": "unknown", "nodes": [{"id": "n", "label": "N", "lane": "source"}], "edges": []},
                ]
            }

    class Request:
        def __init__(self, section, title, purpose):
            self.section, self.title, self.purpose = section, title, purpose

    flows = plan_flows(Brief(subject="S", about="A"), [Request("level_2", "Level 2", "end to end")], JsonLLM())
    assert list(flows) == ["level_2"]
    spec = flows["level_2"]
    assert [n.id for n in spec.nodes] == ["a_b", "c"] and len(spec.edges) == 1 and spec.edges[0].source == "a_b"
    assert plan_flows(Brief(subject="S"), [Request("level_2", "", "")], MockLLM()) == {}
    assert plan_flows(Brief(subject="S"), [], MockLLM()) == {}
    assert clean_flow(FlowSpec(nodes=[FlowNode(id="x", label=" ", lane="source")])).nodes == []


def test_sap_detection_and_icon_keys(tmp_path):
    from sdgen.brief import Brief
    from sdgen.flow import clean_flow, plan_flows, uses_sap
    from sdgen.icons import icon_keys
    from sdgen.plan import FlowRequest

    assert uses_sap(Brief(subject="Lockbox", about="Files go from the bank to SAP S/4HANA."))
    assert not uses_sap(Brief(subject="Payroll", about="Files go from the bank to the payroll provider."))
    spec = FlowSpec(nodes=[FlowNode(id="s4", label="S/4HANA", icon="s4hana"), FlowNode(id="x", label="X", icon="nonsense")])
    cleaned = clean_flow(spec)
    assert [n.icon for n in cleaned.nodes] == ["s4hana", ""]
    cleaned.save(tmp_path / "f.yaml")
    assert FlowSpec.load(tmp_path / "f.yaml") == cleaned

    class Catcher:
        name = "fake"

        def complete(self, system, user):
            return ""

        def complete_json(self, system, user, schema, name="result"):
            self.user, self.schema = user, schema
            return {"flows": [{"section": "level_2", "nodes": [{"id": "a", "label": "A", "lane": "source", "icon": "bank"}, {"id": "b", "label": "B", "lane": "target", "icon": "s4hana"}], "edges": [{"source": "a", "target": "b"}]}]}

    request = FlowRequest(section="level_2", title="L2", purpose="")
    llm = Catcher()
    flows = plan_flows(Brief(subject="x"), [request], llm, icons=icon_keys())
    node_schema = llm.schema["properties"]["flows"]["items"]["properties"]["nodes"]["items"]["properties"]
    assert "Icon keys" in llm.user and node_schema["icon"]["enum"] == icon_keys()
    assert [n.icon for n in flows["level_2"].nodes] == ["bank", "s4hana"]
    plain = Catcher()
    plan_flows(Brief(subject="x"), [request], plain)
    assert "Icon keys" not in plain.user and "icon" not in plain.schema["properties"]["flows"]["items"]["properties"]["nodes"]["items"]["properties"]


def test_draw_flow_places_icon_pictures(tmp_path, monkeypatch):
    from PIL import Image
    from pptx.util import Inches

    import sdgen.flow as flow_module

    png = tmp_path / "icon.png"
    Image.new("RGB", (32, 32), "blue").save(png)
    monkeypatch.setattr(flow_module, "icon_png", lambda key: png if key == "s4hana" else None)
    spec = FlowSpec(nodes=[FlowNode(id="s4", label="S/4HANA", lane="target", icon="s4hana"), FlowNode(id="bank", label="Bank", lane="source")], edges=[FlowEdge(source="bank", target="s4")])
    created = draw_flow(_blank_slide()[1], (Inches(1), Inches(1), Inches(10), Inches(4)), spec, prefix="Flow x")
    names = [s.name for s in created]
    assert "Flow x icon s4" in names and "Flow x icon bank" not in names
    node = next(s for s in created if s.name == "Flow x node s4")
    assert node.text_frame.margin_left > Inches(0.3)
