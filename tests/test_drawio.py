from lxml import etree

from sdgen.drawio import to_drawio
from sdgen.flow import FlowEdge, FlowNode, FlowSpec

SPEC = FlowSpec(
    title="Lockbox",
    nodes=[
        FlowNode(id="bank", label="Bank", lane="source", kind="external"),
        FlowNode(id="btp", label="BTP", lane="middleware", icon="btp_integration_suite"),
        FlowNode(id="s4", label="S/4HANA", lane="target", kind="store"),
    ],
    edges=[FlowEdge(source="bank", target="btp", label="file", kind="file"), FlowEdge(source="btp", target="s4", label="SFTP")],
)
LANES = ("source", "middleware", "target")


def _cells(spec, icons):
    root = etree.fromstring(to_drawio(spec, icons=icons).encode("utf-8"))
    return root, {c.get("id"): c for c in root.findall(".//mxCell")}


def test_drawio_draws_containers_in_the_sap_style():
    root, cells = _cells(SPEC, {})
    assert root.tag == "mxfile" and root.find("diagram").get("name") == "Lockbox"
    lanes = [cells[f"lane_{lane}"] for lane in LANES]
    assert all("rounded=1" in c.get("style") and "arcSize=24" in c.get("style") and "container=1" in c.get("style") for c in lanes)
    assert [cells[f"lane_{lane}_title"].get("value") for lane in LANES] == ["Source", "Middleware", "Target"]
    assert "fillColor=#F5F6F7" in lanes[0].get("style") and "lane_source_logo" not in cells
    for lane in lanes[1:]:
        assert "fillColor=#EBF8FF" in lane.get("style") and "strokeColor=#0070F2" in lane.get("style")
    assert cells["lane_middleware_logo"].get("parent") == "lane_middleware" and "SAP_Logo.svg" in cells["lane_middleware_logo"].get("style")
    x = [float(cells[f"lane_{lane}"].find("mxGeometry").get("x")) for lane in LANES]
    assert x[0] < x[1] < x[2]
    nodes = [cells["n_bank"], cells["n_btp"], cells["n_s4"]]
    assert [c.get("value") for c in nodes] == ["<b>Bank</b>", "<b>BTP</b>", "<b>S/4HANA</b>"]
    assert [c.get("parent") for c in nodes] == ["lane_source", "lane_middleware", "lane_target"]
    assert all("shape=" not in c.get("style") for c in nodes)
    assert "dashPattern=1 2" in nodes[0].get("style") and "fillColor=#F5F6F7" in nodes[0].get("style")
    assert "strokeColor=#0070F2" in nodes[2].get("style") and "fillcolor=#ffffff" in nodes[2].get("style").lower() and "dashed=1" not in nodes[2].get("style")
    edges = [c for c in root.findall(".//mxCell") if c.get("edge") == "1"]
    assert "dashed=1" in edges[0].get("style") and "dashed=1" not in edges[1].get("style") and edges[1].get("source") == "n_btp"
    assert all("strokeColor=#475E74" in c.get("style") and "endArrow=block" in c.get("style") for c in edges)


def test_drawio_embeds_icon_files_inside_the_node(tmp_path):
    svg = tmp_path / "suite.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>', encoding="utf-8")
    root, cells = _cells(SPEC, {"btp_integration_suite": svg})
    style = cells["n_btp"].get("style")
    assert "shape=label" in style and "image=data:image/svg+xml," in style and ";base64" not in style and "spacingLeft=36" in style
    assert cells["n_btp"].get("value") == "<b>BTP</b>"


def test_drawio_writes_subtitles_and_headings():
    named = SPEC.model_copy(update={"nodes": [SPEC.nodes[0].model_copy(update={"subtitle": "SWIFT FileAct"})] + SPEC.nodes[1:], "lanes": {"source": "Bank & partners"}})
    root, cells = _cells(named, {})
    assert cells["n_bank"].get("value") == "<b>Bank</b><br/><span style='font-size:10px;color:#595959'>SWIFT FileAct</span>"
    assert cells["lane_source_title"].get("value") == "Bank &amp; partners"
    two = SPEC.model_copy(update={"nodes": [n for n in SPEC.nodes if n.lane != "target"], "edges": SPEC.edges[:1]})
    root, cells = _cells(two, {})
    assert "lane_target" not in cells and "lane_middleware" in cells


def test_drawio_adds_the_reference_footnote():
    root, cells = _cells(SPEC.model_copy(update={"reference": "sap:RA0021"}), {})
    note = cells["reference"]
    assert note.get("value") == "Reference: SAP Architecture Center, Application to Application Integration (https://architecture.learning.sap.com/docs/ref-arch/6501d5)"
    lane = cells["lane_target"].find("mxGeometry")
    assert float(note.find("mxGeometry").get("y")) >= float(lane.get("y")) + float(lane.get("height"))
    assert "reference" not in _cells(SPEC, {})[1] and "reference" not in _cells(SPEC.model_copy(update={"reference": "sap:RA9999"}), {})[1]
