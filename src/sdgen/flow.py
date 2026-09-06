from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from lxml import etree
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt
from pydantic import BaseModel, Field, ValidationError

from sdgen.brief import Brief, dump_brief
from sdgen.icons import icon_keys, icon_png
from sdgen.llm import LLMClient
from sdgen.textmetrics import FontSpec, text_width_pt

Lane = Literal["source", "middleware", "target"]
NodeKind = Literal["system", "step", "store", "external"]
EdgeKind = Literal["sync", "async", "file"]
LANES: tuple[str, ...] = ("source", "middleware", "target")
LANE_TITLES = {"source": "Source", "middleware": "Middleware", "target": "Target"}
SAP_RE = re.compile(r"\bSAP\b|S/4|S4HANA|\bECC\b|\bBTP\b|Integration Suite|\bCPI\b|PI/PO|Cloud Connector|IDoc|\bRFC\b|OData", re.IGNORECASE)
ICON_PAD = Inches(0.06)
ICON_MAX = Inches(0.45)

NODE_HEIGHT = Inches(0.7)
NODE_WIDTH = Inches(3.0)
MIN_NODE = Inches(0.4)
GAP = Inches(0.35)
HEADER = Inches(0.35)
PAD = Inches(0.15)
LABEL_HEIGHT = Inches(0.22)
LABEL_PAD = Inches(0.12)
LABEL_FONT = FontSpec("Arial", 8.0, False)
LABEL_SMALL_FONT = FontSpec("Arial", 7.0, False)
COLUMNS_MIN_WIDTH = Inches(8)
CHANNEL = Inches(0.45)
LANE_LABEL = Inches(0.55)
ROUTE_OFFSET = Inches(0.2)
ROUTE_STEP = Inches(0.3)
MIN_ROW_NODE = Inches(0.45)
NODES_PER_SQ_IN = 3.5
SMALL_AREA_SQ_IN = 20.0
FILL = {
    "system": RGBColor(0xDC, 0xE8, 0xF7),
    "step": RGBColor(0xEE, 0xEE, 0xEE),
    "store": RGBColor(0xDF, 0xF0, 0xDF),
    "external": RGBColor(0xFF, 0xFF, 0xFF),
}
SHAPES = {
    "system": MSO_SHAPE.ROUNDED_RECTANGLE,
    "step": MSO_SHAPE.RECTANGLE,
    "store": MSO_SHAPE.CAN,
    "external": MSO_SHAPE.ROUNDED_RECTANGLE,
}
LINE = RGBColor(0x40, 0x40, 0x40)
TEXT = RGBColor(0x20, 0x20, 0x20)
TOP, LEFT, BOTTOM, RIGHT = 0, 1, 2, 3

FLOW_SCHEMA = {
    "type": "object",
    "properties": {
        "flows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "title": {"type": "string"},
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "kind": {"type": "string", "enum": ["system", "step", "store", "external"]},
                                "lane": {"type": "string", "enum": list(LANES)},
                            },
                            "required": ["id", "label", "lane"],
                        },
                    },
                    "edges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "target": {"type": "string"},
                                "label": {"type": "string"},
                                "kind": {"type": "string", "enum": ["sync", "async", "file"]},
                            },
                            "required": ["source", "target"],
                        },
                    },
                    "notes": {"type": "string"},
                },
                "required": ["section", "nodes", "edges"],
            },
        }
    },
    "required": ["flows"],
}

SYSTEM_PROMPT = """You design integration flow diagrams for a solution-design document.
For each requested diagram return its nodes and edges.
Nodes: four to nine, fewer when the diagram line caps them, each with a short label (at most
four words), a kind (system, step, store or external) and a lane that follows the direction of
the data: source, middleware or target. Steps that happen inside a system go in that system's
lane, right after it. Small drawing areas get at most two lanes and few nodes.
Edges: from node to node in flow order, each with a short label (at most three words) naming
the protocol, format or trigger, and a kind: sync, async or file.
Use only systems, protocols and steps named in the brief and the facts; never example names.
Return only JSON matching the schema."""


class FlowNode(BaseModel):
    id: str
    label: str
    kind: NodeKind = "system"
    lane: Lane = "middleware"
    icon: str = ""  # a key of the icon catalogue, empty for a plain shape


class FlowEdge(BaseModel):
    source: str
    target: str
    label: str = ""
    kind: EdgeKind = "sync"


class FlowSpec(BaseModel):
    title: str = ""
    nodes: list[FlowNode] = Field(default_factory=list)
    edges: list[FlowEdge] = Field(default_factory=list)
    notes: str = ""

    def save(self, path: str | Path) -> None:
        Path(path).write_text(yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> FlowSpec:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def uses_sap(brief: Brief) -> bool:
    """Whether the brief talks about an SAP landscape, which turns the SAP icon set on."""
    return bool(SAP_RE.search(dump_brief(brief)))


def plan_flows(brief: Brief, requests: list, llm: LLMClient, icons: list[str] | None = None) -> dict[str, FlowSpec]:
    """One model call for all requested diagrams; each request has section, title and purpose.

    With `icons`, every node also gets the closest key of that catalogue.
    """
    if not requests:
        return {}
    lines = ["# Diagrams to design (section key | title | purpose | drawing area)"]
    for request in requests:
        lines.append(f"- {request.section} | {request.title or request.section} | {request.purpose or ''} | {_area_hint(request)}")
    schema = FLOW_SCHEMA
    if icons:
        schema = _schema_with_icons(icons)
        lines += ["", "# Icon keys: give every node the closest one, generic when none fits", ", ".join(icons)]
    lines += ["", "# Facts", brief.facts_text() or "(none)", "", "# Brief", dump_brief(brief)]
    data = llm.complete_json(SYSTEM_PROMPT, "\n".join(lines), schema, name="flows")
    wanted = {request.section for request in requests}
    result: dict[str, FlowSpec] = {}
    for item in data.get("flows") or []:
        key = str(item.get("section") or "")
        if key not in wanted:
            continue
        try:
            spec = FlowSpec.model_validate({k: item.get(k) or ([] if k in ("nodes", "edges") else "") for k in ("title", "nodes", "edges", "notes")})
        except ValidationError:
            continue
        spec = clean_flow(spec)
        if spec.nodes:
            result[key] = spec
    return result


def node_cap(width_in: float, height_in: float) -> int:
    """Nodes a drawing area can show readably."""
    return max(4, min(9, int(width_in * height_in / NODES_PER_SQ_IN)))


def _area_hint(request) -> str:
    width, height = float(getattr(request, "width_in", 0) or 0), float(getattr(request, "height_in", 0) or 0)
    if not width or not height:
        return "size unknown, at most 9 nodes"
    hint = f"drawing area {width:.1f} x {height:.1f} in, at most {node_cap(width, height)} nodes"
    return hint + (", two lanes at most" if width * height < SMALL_AREA_SQ_IN else "")


def _schema_with_icons(icons: list[str]) -> dict:
    import copy

    schema = copy.deepcopy(FLOW_SCHEMA)
    schema["properties"]["flows"]["items"]["properties"]["nodes"]["items"]["properties"]["icon"] = {"type": "string", "enum": list(icons)}
    return schema


def clean_flow(spec: FlowSpec) -> FlowSpec:
    seen: set[str] = set()
    known = set(icon_keys())
    nodes = []
    for node in spec.nodes:
        node_id = _ident(node.id)
        if not node_id or node_id in seen or not node.label.strip():
            continue
        seen.add(node_id)
        nodes.append(node.model_copy(update={"id": node_id, "label": " ".join(node.label.split()), "icon": node.icon if node.icon in known else ""}))
    edges = []
    for edge in spec.edges:
        source, target = _ident(edge.source), _ident(edge.target)
        if source in seen and target in seen and source != target:
            edges.append(edge.model_copy(update={"source": source, "target": target, "label": " ".join(edge.label.split())}))
    return spec.model_copy(update={"nodes": nodes, "edges": edges})


def to_mermaid(spec: FlowSpec) -> str:
    lines = ["flowchart LR"]
    for lane in LANES:
        members = [n for n in spec.nodes if n.lane == lane]
        if not members:
            continue
        lines.append(f"  subgraph {lane}[{LANE_TITLES[lane]}]")
        for node in members:
            label = node.label.replace('"', "'")
            if node.kind == "step":
                lines.append(f'    {node.id}(["{label}"])')
            elif node.kind == "store":
                lines.append(f'    {node.id}[("{label}")]')
            elif node.kind == "external":
                lines.append(f'    {node.id}{{{{"{label}"}}}}')
            else:
                lines.append(f'    {node.id}["{label}"]')
        lines.append("  end")
    arrows = {"sync": "-->", "async": "-.->", "file": "==>"}
    for edge in spec.edges:
        arrow = arrows.get(edge.kind, "-->")
        label = f"|{edge.label}|" if edge.label else ""
        lines.append(f"  {edge.source} {arrow}{label} {edge.target}")
    return "\n".join(lines) + "\n"


def draw_flow(slide, box: tuple[int, int, int, int], spec: FlowSpec, prefix: str = "Flow") -> list:
    """Draws the flow as editable shapes inside the box (EMU left, top, width, height)."""
    left, top, width, height = box
    lanes = [lane for lane in LANES if any(n.lane == lane for n in spec.nodes)] or ["middleware"]
    index_of = {lane: i for i, lane in enumerate(lanes)}
    lane_of = {n.id: index_of.get(n.lane, 0) for n in spec.nodes}
    far = any(abs(lane_of[e.source] - lane_of[e.target]) >= 2 for e in spec.edges if e.source in lane_of and e.target in lane_of)
    columns = width >= COLUMNS_MIN_WIDTH or len(lanes) == 1
    if not columns and (height - 2 * PAD) // len(lanes) - 2 * PAD < MIN_ROW_NODE:
        columns = True  # too flat for rows: columns keep every node readable
    created = [_canvas(slide, left, top, width, height, f"{prefix} canvas")]
    placed: dict[str, tuple] = {}
    left, top, width, height = left + PAD, top + PAD, width - 2 * PAD, height - 2 * PAD
    channel = CHANNEL if far else 0
    geometry = {"columns": columns, "left": left, "top": top, "width": width, "height": height, "lanes": len(lanes)}
    if columns:
        lane_width = width // len(lanes)
        node_width = min(NODE_WIDTH, lane_width - 2 * PAD)
        geometry.update(lane_width=lane_width, channel_y=top + height - channel // 2)
        for index, lane in enumerate(lanes):
            x0 = left + index * lane_width
            created.append(_header(slide, x0, top, lane_width, HEADER, LANE_TITLES[lane], f"{prefix} lane {lane}"))
            members = [n for n in spec.nodes if n.lane == lane]
            node_height, gap = _fit(len(members), height - channel - HEADER - PAD, NODE_HEIGHT, GAP)
            y = top + HEADER + PAD
            for position, node in enumerate(members):
                shape = _node(slide, node, x0 + (lane_width - node_width) // 2, y, node_width, node_height, prefix, created)
                placed[node.id] = (shape, index, position)
                created.append(shape)
                y += node_height + gap
    else:
        lane_height = height // len(lanes)
        usable = width - LANE_LABEL - channel
        geometry.update(lane_height=lane_height, channel_x=left + width - channel // 2)
        for index, lane in enumerate(lanes):
            y0 = top + index * lane_height
            created.append(_lane_label(slide, left, y0, LANE_LABEL, lane_height, LANE_TITLES[lane], f"{prefix} lane {lane}"))
            members = [n for n in spec.nodes if n.lane == lane]
            node_width, gap = _fit(len(members), usable - 2 * PAD, NODE_WIDTH, GAP)
            node_height = max(MIN_NODE, min(NODE_HEIGHT, lane_height - 2 * PAD))
            x = left + LANE_LABEL + PAD
            for position, node in enumerate(members):
                shape = _node(slide, node, x, y0 + (lane_height - node_height) // 2, node_width, node_height, prefix, created)
                placed[node.id] = (shape, index, position)
                created.append(shape)
                x += node_width + gap
    routed: dict[int, int] = {}
    for number, edge in enumerate(spec.edges, 1):
        start, end = placed.get(edge.source), placed.get(edge.target)
        if start is None or end is None:
            continue
        created.extend(_edge(slide, start, end, geometry, routed, edge, f"{prefix} edge {number}"))
    return created


def _fit(count: int, available: int, size: int, gap: int) -> tuple[int, int]:
    if count <= 1:
        return min(size, max(MIN_NODE, available)), gap
    if count * size + (count - 1) * gap <= available:
        return size, gap
    gap = gap // 2
    return max(MIN_NODE, (available - (count - 1) * gap) // count), gap


def _canvas(slide, x: int, y: int, width: int, height: int, name: str):
    # A white board behind the diagram keeps it readable on dark slide backgrounds.
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, width, height)
    shape.name = name
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    shape.line.color.rgb = RGBColor(0xBF, 0xBF, 0xBF)
    shape.line.width = Pt(0.75)
    shape.shadow.inherit = False
    shape.text_frame.text = ""
    return shape


def _header(slide, x: int, y: int, width: int, height: int, text: str, name: str):
    box = slide.shapes.add_textbox(x, y, width, height)
    box.name = name
    frame = box.text_frame
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.text = text
    paragraph.alignment = PP_ALIGN.CENTER
    for run in paragraph.runs:
        run.font.size = Pt(10)
        run.font.bold = True
        run.font.color.rgb = LINE
    return box


def _lane_label(slide, x: int, y: int, width: int, height: int, text: str, name: str):
    """A lane title standing on its side along the left edge, so the lane keeps its full height for nodes."""
    box = _header(slide, x + width // 2 - height // 2, y + height // 2 - width // 2, height, width, text, name)
    box.rotation = 270.0
    box.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    box.text_frame.word_wrap = False
    for run in box.text_frame.paragraphs[0].runs:
        run.font.size = Pt(9)
    return box


def _node(slide, node: FlowNode, x: int, y: int, width: int, height: int, prefix: str, created: list | None = None):
    shape = slide.shapes.add_shape(SHAPES.get(node.kind, MSO_SHAPE.ROUNDED_RECTANGLE), x, y, width, height)
    shape.name = f"{prefix} node {node.id}"
    shape.fill.solid()
    shape.fill.fore_color.rgb = FILL.get(node.kind, FILL["system"])
    shape.line.color.rgb = LINE
    shape.line.width = Pt(1)
    if node.kind == "external":
        shape.line.dash_style = MSO_LINE.DASH
    frame = shape.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    frame.margin_left = frame.margin_right = Inches(0.05)
    frame.margin_top = frame.margin_bottom = Inches(0.03)
    png = icon_png(node.icon) if node.icon else None
    if png is not None:
        size = max(Inches(0.2), min(ICON_MAX, height - 2 * ICON_PAD))
        picture = slide.shapes.add_picture(str(png), x + ICON_PAD, y + (height - size) // 2, size, size)
        picture.name = f"{prefix} icon {node.id}"
        frame.margin_left = size + 2 * ICON_PAD
        if created is not None:
            created.append(picture)
    paragraph = frame.paragraphs[0]
    paragraph.text = node.label
    paragraph.alignment = PP_ALIGN.CENTER
    size = Pt(11) if len(node.label) <= 24 else Pt(9)
    for run in paragraph.runs:
        run.font.size = size
        run.font.color.rgb = TEXT
    return shape


def _edge(slide, start: tuple, end: tuple, geometry: dict, routed: dict[int, int], edge: FlowEdge, name: str) -> list:
    """Neighbours get a glued connector; anything that would cross a node is routed around it."""
    shape_a, lane_a, pos_a = start
    shape_b, lane_b, pos_b = end
    columns = geometry["columns"]
    if lane_a == lane_b and abs(pos_a - pos_b) == 1:
        if columns:
            sites = (BOTTOM, TOP) if pos_b > pos_a else (TOP, BOTTOM)
        else:
            sites = (RIGHT, LEFT) if pos_b > pos_a else (LEFT, RIGHT)
        return _connector(slide, shape_a, shape_b, sites, edge, name)
    if abs(lane_a - lane_b) == 1:
        if columns:
            sites = (RIGHT, LEFT) if lane_b > lane_a else (LEFT, RIGHT)
        else:
            sites = (BOTTOM, TOP) if lane_b > lane_a else (TOP, BOTTOM)
        return _connector(slide, shape_a, shape_b, sites, edge, name)
    if lane_a == lane_b:
        last = lane_a == geometry["lanes"] - 1
        points = _side_route(shape_a, shape_b, columns, routed.get(lane_a, 0), above=not columns and last)
        routed[lane_a] = routed.get(lane_a, 0) + 1
        return _polyline(slide, points, edge, name, "along" if columns else ("above" if last else "below"))
    return _polyline(slide, _channel_route(shape_a, shape_b, lane_a, lane_b, geometry), edge, name, "on")


def _connector(slide, shape_a, shape_b, sites: tuple[int, int], edge: FlowEdge, name: str) -> list:
    site_a, site_b = sites
    x1, y1 = _site(shape_a, site_a)
    x2, y2 = _site(shape_b, site_b)
    connector = slide.shapes.add_connector(MSO_CONNECTOR.ELBOW, x1, y1, x2, y2)
    connector.name = name
    connector.begin_connect(shape_a, site_a)
    connector.end_connect(shape_b, site_b)
    _style_line(connector, edge)
    if site_a in (LEFT, RIGHT):
        mid = (x1 + x2) // 2
        points = [(x1, y1), (mid, y1), (mid, y2), (x2, y2)] if y1 != y2 else [(x1, y1), (x2, y2)]
    else:
        mid = (y1 + y2) // 2
        points = [(x1, y1), (x1, mid), (x2, mid), (x2, y2)] if x1 != x2 else [(x1, y1), (x2, y2)]
    return [connector] + _label(slide, points, edge.label, name, "on", min(shape_a.top, shape_b.top))


def _polyline(slide, points: list[tuple[int, int]], edge: FlowEdge, name: str, placement: str = "on") -> list:
    builder = slide.shapes.build_freeform(points[0][0], points[0][1], scale=1.0)
    builder.add_line_segments(points[1:], close=False)
    shape = builder.convert_to_shape()
    shape.name = name
    shape.fill.background()
    _style_line(shape, edge)
    return [shape] + _label(slide, points, edge.label, name, placement)


def _style_line(shape, edge: FlowEdge) -> None:
    shape.line.color.rgb = LINE
    shape.line.width = Pt(1.25)
    if edge.kind != "sync":
        shape.line.dash_style = MSO_LINE.DASH
    _arrow_head(shape)


def _side_route(a, b, columns: bool, staggered: int, above: bool = False) -> list[tuple[int, int]]:
    """Out of the side, past the nodes in between, back into the target's side."""
    offset = ROUTE_OFFSET + ROUTE_STEP * staggered
    if columns:
        x = max(a.left + a.width, b.left + b.width) + offset
        return [(a.left + a.width, _cy(a)), (x, _cy(a)), (x, _cy(b)), (b.left + b.width, _cy(b))]
    if above:
        y = min(a.top, b.top) - offset
        return [(_cx(a), a.top), (_cx(a), y), (_cx(b), y), (_cx(b), b.top)]
    y = max(a.top + a.height, b.top + b.height) + offset
    return [(_cx(a), a.top + a.height), (_cx(a), y), (_cx(b), y), (_cx(b), b.top + b.height)]


def _channel_route(a, b, lane_a: int, lane_b: int, geometry: dict) -> list[tuple[int, int]]:
    """Through the gap next to the start, along the free channel past the lanes in between, into the target."""
    forward = lane_b > lane_a
    if geometry["columns"]:
        boundary = lambda i: geometry["left"] + i * geometry["lane_width"] + ROUTE_STEP  # noqa: E731
        y = geometry["channel_y"]
        if forward:
            x_out, x_in = boundary(lane_a + 1), boundary(lane_b)
            return [(a.left + a.width, _cy(a)), (x_out, _cy(a)), (x_out, y), (x_in, y), (x_in, _cy(b)), (b.left, _cy(b))]
        x_out, x_in = boundary(lane_a), boundary(lane_b + 1)
        return [(a.left, _cy(a)), (x_out, _cy(a)), (x_out, y), (x_in, y), (x_in, _cy(b)), (b.left + b.width, _cy(b))]
    boundary = lambda i: geometry["top"] + i * geometry["lane_height"] + ROUTE_STEP  # noqa: E731
    x = geometry["channel_x"]
    if forward:
        y_out, y_in = boundary(lane_a + 1), boundary(lane_b)
        return [(_cx(a), a.top + a.height), (_cx(a), y_out), (x, y_out), (x, y_in), (_cx(b), y_in), (_cx(b), b.top)]
    y_out, y_in = boundary(lane_a), boundary(lane_b + 1)
    return [(_cx(a), a.top), (_cx(a), y_out), (x, y_out), (x, y_in), (_cx(b), y_in), (_cx(b), b.top + b.height)]


def _label(slide, points: list[tuple[int, int]], text: str, name: str, placement: str = "on", lift_above: int | None = None) -> list:
    """A white label sized to its text on the longest segment.

    `placement` is "on" the line, "along" a vertical line (text turned upright), "below" or "above" a
    horizontal one; a straight horizontal line too short for the label moves it above the nodes when
    `lift_above` gives their top.
    """
    if not text:
        return []
    (p, q) = max(zip(points, points[1:]), key=lambda s: abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1]))
    mx, my = (p[0] + q[0]) // 2, (p[1] + q[1]) // 2
    font = LABEL_FONT if len(text) <= 24 else LABEL_SMALL_FONT
    width = int(text_width_pt(text, font) * 12700) + LABEL_PAD
    height = LABEL_HEIGHT
    lx, ly = mx - width // 2, my - height // 2
    rotation = 0.0
    if p[0] == q[0]:
        rotation = 270.0 if placement == "along" else 0.0
    elif placement == "below":
        ly = my + Inches(0.05)
    elif placement == "above":
        ly = my - height - Inches(0.05)
    elif lift_above is not None and len(points) == 2 and abs(q[0] - p[0]) < width + Inches(0.05):
        ly = lift_above - height - Inches(0.02)
    label = slide.shapes.add_textbox(lx, ly, width, height)
    label.rotation = rotation
    label.name = f"{name} label"
    label.fill.solid()
    label.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    label.line.fill.background()
    frame = label.text_frame
    frame.word_wrap = False
    frame.margin_left = frame.margin_right = Inches(0.03)
    frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]
    paragraph.text = text
    paragraph.alignment = PP_ALIGN.CENTER
    for run in paragraph.runs:
        run.font.size = Pt(font.size_pt)
        run.font.color.rgb = TEXT
    return [label]


def _cx(shape) -> int:
    return shape.left + shape.width // 2


def _cy(shape) -> int:
    return shape.top + shape.height // 2


def _site(shape, site: int) -> tuple[int, int]:
    x, y, w, h = shape.left, shape.top, shape.width, shape.height
    return {TOP: (x + w // 2, y), LEFT: (x, y + h // 2), BOTTOM: (x + w // 2, y + h), RIGHT: (x + w, y + h // 2)}[site]


def _arrow_head(connector) -> None:
    ln = connector.line._get_or_add_ln()
    tail = ln.find(qn("a:tailEnd"))
    if tail is None:
        tail = etree.SubElement(ln, qn("a:tailEnd"))
    tail.set("type", "triangle")


def _ident(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).strip("_")
