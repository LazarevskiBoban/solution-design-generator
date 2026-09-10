from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.util import Inches

from sdgen.brief import Brief
from sdgen.flow import SYSTEM_PROMPT, FlowEdge, FlowNode, FlowSpec, clean_flow, draw_flow, plan_flows, to_mermaid
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
    # A node too narrow for icon plus label keeps the label width and gets a corner badge instead.
    narrow: list = []
    slide = _blank_slide()[1]
    small = flow_module._node(slide, FlowNode(id="s4", label="S/4HANA", icon="s4hana"), 0, 0, Inches(0.6), Inches(0.5), "Flow y", narrow)
    assert small.text_frame.margin_left == Inches(0.05) and narrow[0].width == flow_module.ICON_BADGE


def _box(shape):
    return (shape.left, shape.top, shape.left + shape.width, shape.top + shape.height)


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _labels_clear_of_nodes(created) -> bool:
    nodes = [s for s in created if " node " in s.name]
    labels = [s for s in created if s.name.endswith(" label")]
    return not any(_overlaps(_box(label), _box(node)) for label in labels for node in nodes)


def test_edges_that_skip_a_node_go_around_it():
    prs, slide = _blank_slide()
    spec = FlowSpec(
        nodes=[
            FlowNode(id="a", label="A", lane="middleware"),
            FlowNode(id="b", label="B", lane="middleware"),
            FlowNode(id="c", label="C", lane="middleware"),
            FlowNode(id="s", label="S", lane="source"),
            FlowNode(id="t", label="T", lane="target"),
        ],
        edges=[
            FlowEdge(source="a", target="b", label="one"),
            FlowEdge(source="b", target="c", label="two"),
            FlowEdge(source="a", target="c", label="skip"),
            FlowEdge(source="s", target="t", label="far"),
            FlowEdge(source="s", target="a", label="in"),
        ],
    )
    created = draw_flow(slide, (Inches(0.5), Inches(1.0), Inches(12.0), Inches(5.5)), spec, prefix="Flow x")
    by_name = {s.name: s for s in created}
    a, b = by_name["Flow x node a"], by_name["Flow x node b"]
    skip = by_name["Flow x edge 3"]
    assert skip._element.find(".//" + qn("a:stCxn")) is None and skip._element.find(".//" + qn("a:tailEnd")).get("type") == "triangle"
    assert skip.left >= b.left + b.width - 1
    far = by_name["Flow x edge 4"]
    lowest = max(s.top + s.height for s in created if " node " in s.name)
    assert far.top + far.height > lowest and far.left < a.left and far.left + far.width > a.left + a.width
    assert by_name["Flow x edge 5"]._element.find(".//" + qn("a:stCxn")) is not None
    assert _labels_clear_of_nodes(created)
    canvas = _box(by_name["Flow x canvas"])
    for shape in created:
        if " node " in shape.name or " edge " in shape.name and not shape.name.endswith("label"):
            box = _box(shape)
            assert box[0] >= canvas[0] - 1 and box[1] >= canvas[1] - 1 and box[2] <= canvas[2] + 1 and box[3] <= canvas[3] + 1


def test_rows_layout_labels_lanes_on_the_side_and_routes_far_edges():
    from sdgen.flow import LANE_LABEL

    prs, slide = _blank_slide()
    spec = FlowSpec(
        nodes=[FlowNode(id="s", label="S", lane="source"), FlowNode(id="m1", label="M1", lane="middleware"), FlowNode(id="m2", label="M2", lane="middleware"), FlowNode(id="t", label="T", lane="target")],
        edges=[FlowEdge(source="s", target="m1", label="a"), FlowEdge(source="m1", target="m2", label="b"), FlowEdge(source="m2", target="t", label="c"), FlowEdge(source="s", target="t", label="far")],
    )
    created = draw_flow(slide, (Inches(0.5), Inches(1.0), Inches(6.0), Inches(5.5)), spec, prefix="Flow r")
    by_name = {s.name: s for s in created}
    assert by_name["Flow r lane source"].rotation == 270.0
    s, t = by_name["Flow r node s"], by_name["Flow r node t"]
    assert s.top < t.top and s.left >= Inches(0.5) + LANE_LABEL
    far = by_name["Flow r edge 4"]
    rightmost = max(n.left + n.width for n in created if " node " in n.name)
    assert far.left + far.width > rightmost and far._element.find(".//" + qn("a:stCxn")) is None
    assert _labels_clear_of_nodes(created)


def test_flat_box_falls_back_to_columns():
    prs, slide = _blank_slide()
    created = draw_flow(slide, (Inches(0.5), Inches(1.0), Inches(6.0), Inches(1.8)), SPEC, prefix="Flow f")
    bank = next(s for s in created if s.name == "Flow f node bank")
    s4 = next(s for s in created if s.name == "Flow f node s4")
    assert bank.left < s4.left and bank.top == s4.top and bank.height >= Inches(0.4)


def test_plan_flows_tells_the_model_the_drawing_area():
    from sdgen.flow import node_cap
    from sdgen.plan import FlowRequest

    class Catcher:
        name = "fake"

        def complete(self, system, user):
            return ""

        def complete_json(self, system, user, schema, name="result"):
            self.user = user
            return {"flows": []}

    llm = Catcher()
    requests = [
        FlowRequest(section="a", title="A", purpose="p", width_in=12.7, height_in=5.5),
        FlowRequest(section="b", title="B", purpose="q", width_in=6.3, height_in=2.6),
        FlowRequest(section="c", title="C", purpose=""),
    ]
    plan_flows(Brief(subject="x"), requests, llm)
    assert "drawing area 12.7 x 5.5 in, at most 9 nodes" in llm.user
    assert "drawing area 6.3 x 2.6 in, at most 4 nodes, two lanes at most" in llm.user
    assert "size unknown, at most 9 nodes" in llm.user
    assert node_cap(12.7, 5.5) == 9 and node_cap(6.3, 2.6) == 4 and node_cap(7, 4) == 8


def test_steps_are_kept_or_derived_from_the_edges():
    from sdgen.flow import flow_steps, walkthrough_text
    from sdgen.plan import FlowRequest

    assert flow_steps(SPEC)[:2] == ["Bank (SWIFT FileAct) to AutoClient VM: lockbox file.", "AutoClient VM to Azure Blob SFTP: SFTP put."]
    told = SPEC.model_copy(update={"steps": ["First.", " Second step. ", ""]})
    assert walkthrough_text(told) == "1. First.\n2. Second step."
    assert walkthrough_text(SPEC).startswith("1. Bank (SWIFT FileAct) to AutoClient VM: lockbox file.\n2. ")
    assert clean_flow(FlowSpec(nodes=[FlowNode(id="a", label="A")], steps=["  two   words ", " "])).steps == ["two words"]

    class Catcher:
        name = "fake"

        def complete(self, system, user):
            return ""

        def complete_json(self, system, user, schema, name="result"):
            assert "steps" in schema["properties"]["flows"]["items"]["properties"]
            return {"flows": [{"section": "x", "nodes": [{"id": "a", "label": "A", "lane": "source"}], "edges": [], "steps": ["A starts.", "A ends."]}]}

    flows = plan_flows(Brief(subject="s"), [FlowRequest(section="x", title="X", purpose="")], Catcher())
    assert flows["x"].steps == ["A starts.", "A ends."]


def test_sap_nodes_lanes_and_headings():
    from sdgen.flow import lane_is_sap, lane_title, node_is_sap

    assert [node_is_sap(n) for n in SPEC.nodes] == [False, False, False, True, True]
    assert [lane_is_sap(SPEC, lane) for lane in ("source", "middleware", "target")] == [False, True, True]
    assert not node_is_sap(FlowNode(id="x", label="SAP S/4HANA", icon="bank")) and node_is_sap(FlowNode(id="y", label="ERP", icon="s4hana"))
    assert node_is_sap(FlowNode(id="z", label="Core ERP", subtitle="SAP ECC 6.0"))
    named = SPEC.model_copy(update={"lanes": {"source": "Bank side", "target": "SAP Landscape (On-Premise)"}})
    assert lane_title(named, "source") == "Bank side" and lane_title(named, "middleware") == "Middleware"
    assert not lane_is_sap(named, "source") and lane_is_sap(named.model_copy(update={"lanes": {"source": "SAP side"}}), "source")
    text = to_mermaid(named)
    assert "  subgraph source[Bank side]" in text and '  subgraph target["SAP Landscape (On-Premise)"]' in text


def _stub(payload):
    class Stub:
        name = "fake"

        def complete(self, system, user):
            return ""

        def complete_json(self, system, user, schema, name="result"):
            self.schema, self.user = schema, user
            return payload

    return Stub()


def test_plan_flows_keeps_subtitles_and_lane_headings(tmp_path):
    from sdgen.plan import FlowRequest

    request = FlowRequest(section="x", title="X", purpose="")
    llm = _stub({"flows": [{"section": "x", "lanes": {"source": " Bank  side ", "middleware": "", "bogus": "No"}, "nodes": [{"id": "a", "label": "Bank", "subtitle": " SWIFT  FileAct ", "lane": "source"}, {"id": "b", "label": "S/4HANA", "lane": "target"}], "edges": [{"source": "a", "target": "b"}]}]})
    flows = plan_flows(Brief(subject="s"), [request], llm)
    flow_schema = llm.schema["properties"]["flows"]["items"]["properties"]
    assert "subtitle" in flow_schema["nodes"]["items"]["properties"] and list(flow_schema["lanes"]["properties"]) == ["source", "middleware", "target"]
    assert "subtitle" in SYSTEM_PROMPT and "Lanes: a heading" in SYSTEM_PROMPT
    spec = flows["x"]
    assert spec.lanes == {"source": "Bank side"} and [n.subtitle for n in spec.nodes] == ["SWIFT FileAct", ""]
    spec.save(tmp_path / "f.yaml")
    assert FlowSpec.load(tmp_path / "f.yaml") == spec
    odd = _stub({"flows": [{"section": "x", "lanes": "none", "nodes": [{"id": "a", "label": "A", "lane": "source"}], "edges": []}]})
    assert plan_flows(Brief(subject="s"), [request], odd)["x"].lanes == {}
