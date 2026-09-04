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
from sdgen.llm import LLMClient

Lane = Literal["source", "middleware", "target"]
NodeKind = Literal["system", "step", "store", "external"]
EdgeKind = Literal["sync", "async", "file"]
LANES: tuple[str, ...] = ("source", "middleware", "target")
LANE_TITLES = {"source": "Source", "middleware": "Middleware", "target": "Target"}

NODE_HEIGHT = Inches(0.7)
NODE_WIDTH = Inches(3.0)
MIN_NODE = Inches(0.4)
GAP = Inches(0.35)
HEADER = Inches(0.35)
PAD = Inches(0.15)
LABEL_WIDTH = Inches(1.1)
LABEL_HEIGHT = Inches(0.3)
COLUMNS_MIN_WIDTH = Inches(8)
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
Nodes: four to nine, each with a short label (at most five words), a kind (system, step,
store or external) and a lane that follows the direction of the data: source, middleware or
target. Steps that happen inside a system go in that system's lane, right after it.
Edges: from node to node in flow order, each with a short label naming the protocol, format
or trigger, and a kind: sync, async or file.
Use only systems, protocols and steps named in the brief and the facts; never example names.
Return only JSON matching the schema."""


class FlowNode(BaseModel):
    id: str
    label: str
    kind: NodeKind = "system"
    lane: Lane = "middleware"


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


def plan_flows(brief: Brief, requests: list, llm: LLMClient) -> dict[str, FlowSpec]:
    """One model call for all requested diagrams; each request has section, title and purpose."""
    if not requests:
        return {}
    lines = ["# Diagrams to design (section key | title | purpose)"]
    for request in requests:
        lines.append(f"- {request.section} | {request.title or request.section} | {request.purpose or ''}")
    lines += ["", "# Facts", brief.facts_text() or "(none)", "", "# Brief", dump_brief(brief)]
    data = llm.complete_json(SYSTEM_PROMPT, "\n".join(lines), FLOW_SCHEMA, name="flows")
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


def clean_flow(spec: FlowSpec) -> FlowSpec:
    seen: set[str] = set()
    nodes = []
    for node in spec.nodes:
        node_id = _ident(node.id)
        if not node_id or node_id in seen or not node.label.strip():
            continue
        seen.add(node_id)
        nodes.append(node.model_copy(update={"id": node_id, "label": " ".join(node.label.split())}))
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
    columns = width >= COLUMNS_MIN_WIDTH or len(lanes) == 1
    created = [_canvas(slide, left, top, width, height, f"{prefix} canvas")]
    placed: dict[str, tuple] = {}
    left, top, width, height = left + PAD, top + PAD, width - 2 * PAD, height - 2 * PAD
    if columns:
        lane_width = width // len(lanes)
        node_width = min(NODE_WIDTH, lane_width - 2 * PAD)
        for index, lane in enumerate(lanes):
            x0 = left + index * lane_width
            created.append(_header(slide, x0, top, lane_width, HEADER, LANE_TITLES[lane], f"{prefix} lane {lane}"))
            members = [n for n in spec.nodes if n.lane == lane]
            node_height, gap = _fit(len(members), height - HEADER - PAD, NODE_HEIGHT, GAP)
            y = top + HEADER + PAD
            for position, node in enumerate(members):
                shape = _node(slide, node, x0 + (lane_width - node_width) // 2, y, node_width, node_height, prefix)
                placed[node.id] = (shape, index, position)
                created.append(shape)
                y += node_height + gap
    else:
        lane_height = height // len(lanes)
        for index, lane in enumerate(lanes):
            y0 = top + index * lane_height
            created.append(_header(slide, left, y0, width, HEADER, LANE_TITLES[lane], f"{prefix} lane {lane}"))
            members = [n for n in spec.nodes if n.lane == lane]
            node_width, gap = _fit(len(members), width - 2 * PAD, NODE_WIDTH, GAP)
            node_height = max(MIN_NODE, min(NODE_HEIGHT, lane_height - HEADER - PAD))
            x = left + PAD
            for position, node in enumerate(members):
                shape = _node(slide, node, x, y0 + HEADER, node_width, node_height, prefix)
                placed[node.id] = (shape, index, position)
                created.append(shape)
                x += node_width + gap
    for number, edge in enumerate(spec.edges, 1):
        start, end = placed.get(edge.source), placed.get(edge.target)
        if start is None or end is None:
            continue
        created.extend(_connect(slide, start, end, columns, edge, f"{prefix} edge {number}"))
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


def _node(slide, node: FlowNode, x: int, y: int, width: int, height: int, prefix: str):
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
    paragraph = frame.paragraphs[0]
    paragraph.text = node.label
    paragraph.alignment = PP_ALIGN.CENTER
    size = Pt(11) if len(node.label) <= 24 else Pt(9)
    for run in paragraph.runs:
        run.font.size = size
        run.font.color.rgb = TEXT
    return shape


def _connect(slide, start: tuple, end: tuple, columns: bool, edge: FlowEdge, name: str) -> list:
    shape_a, lane_a, pos_a = start
    shape_b, lane_b, pos_b = end
    if columns:
        if lane_b > lane_a:
            site_a, site_b = RIGHT, LEFT
        elif lane_b < lane_a:
            site_a, site_b = LEFT, RIGHT
        else:
            site_a, site_b = (BOTTOM, TOP) if pos_b > pos_a else (TOP, BOTTOM)
    else:
        if lane_b > lane_a:
            site_a, site_b = BOTTOM, TOP
        elif lane_b < lane_a:
            site_a, site_b = TOP, BOTTOM
        else:
            site_a, site_b = (RIGHT, LEFT) if pos_b > pos_a else (LEFT, RIGHT)
    x1, y1 = _site(shape_a, site_a)
    x2, y2 = _site(shape_b, site_b)
    connector = slide.shapes.add_connector(MSO_CONNECTOR.ELBOW, x1, y1, x2, y2)
    connector.name = name
    connector.begin_connect(shape_a, site_a)
    connector.end_connect(shape_b, site_b)
    connector.line.color.rgb = LINE
    connector.line.width = Pt(1.25)
    if edge.kind != "sync":
        connector.line.dash_style = MSO_LINE.DASH
    _arrow_head(connector)
    created = [connector]
    if edge.label:
        vertical = abs(y2 - y1) >= abs(x2 - x1)
        if lane_a != lane_b:
            # The elbow leaves the start box first, so the label sits on that first segment.
            if site_a == RIGHT:
                lx, ly = x1 + Inches(0.05), y1 - LABEL_HEIGHT - Inches(0.02)
            elif site_a == LEFT:
                lx, ly = x1 - LABEL_WIDTH - Inches(0.05), y1 - LABEL_HEIGHT - Inches(0.02)
            elif site_a == BOTTOM:
                lx, ly = x1 + Inches(0.08), y1 + Inches(0.02)
            else:
                lx, ly = x1 + Inches(0.08), y1 - LABEL_HEIGHT - Inches(0.02)
            vertical = site_a in (TOP, BOTTOM)
        elif vertical:
            lx, ly = (x1 + x2) // 2 + Inches(0.08), (y1 + y2) // 2 - LABEL_HEIGHT // 2
        else:
            lx, ly = (x1 + x2) // 2 - LABEL_WIDTH // 2, (y1 + y2) // 2 - LABEL_HEIGHT - Inches(0.02)
        label = slide.shapes.add_textbox(lx, ly, LABEL_WIDTH, LABEL_HEIGHT)
        label.name = f"{name} label"
        label.fill.solid()
        label.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        frame = label.text_frame
        frame.word_wrap = True
        frame.margin_left = frame.margin_right = Inches(0.03)
        frame.margin_top = frame.margin_bottom = 0
        paragraph = frame.paragraphs[0]
        paragraph.text = edge.label
        paragraph.alignment = PP_ALIGN.LEFT if vertical else PP_ALIGN.CENTER
        for run in paragraph.runs:
            run.font.size = Pt(9)
            run.font.color.rgb = TEXT
        created.append(label)
    return created


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
