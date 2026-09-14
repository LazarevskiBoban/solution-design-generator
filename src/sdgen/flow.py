from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from lxml import etree
from pptx.enum.dml import MSO_LINE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt
from pydantic import BaseModel, Field, ValidationError

from sdgen.brief import Brief, dump_brief
from sdgen.flowlayout import HEADER, PAD, EdgeIn, FlowLayout, LabelBox, LaneInfo, NodeIn, layout_flow
from sdgen.icons import catalogue, icon_keys, icon_png
from sdgen.llm import LLMClient
from sdgen.material import material_text
from sdgen.grounding import GROUNDING_RULE
from sdgen.palette import EDGE, GREY_FILL, SAP_BLUE, SAP_DARK, SAP_FILL, SLATE, SUBTITLE, TEXT, WHITE, rgb
from sdgen.textmetrics import FontSpec, line_height_pt, text_width_pt, wrapped_lines
from sdgen.references import Pack, candidates, labels, lookup, matching
from sdgen.references import pack as reference_pack

Lane = Literal["source", "middleware", "target"]
LaneRole = Literal["source", "middleware", "target", "other"]
NodeKind = Literal["system", "step", "store", "external"]
EdgeKind = Literal["sync", "async", "file", "error"]
LANES: tuple[str, ...] = ("source", "middleware", "target")
LANE_TITLES = {"source": "Source", "middleware": "Middleware", "target": "Target"}
ROLE_ORDER = {"source": 0, "middleware": 1, "target": 2, "other": 3}
ROLE_WORDS = {"source": ("source", "sender", "origin", "from"), "target": ("target", "receiver", "destination"), "middleware": ("middleware", "platform", "integration", "hub")}
STOP_TOKENS = {"sap", "system", "out", "in", "to", "from", "the", "on", "and", "of", "for", "via"}
_SAP_PACK = reference_pack("sap")
# The SAP pack's detect rule decides what counts as SAP; the literal is the fallback without the pack.
SAP_RE = re.compile(_SAP_PACK.detect if _SAP_PACK is not None and _SAP_PACK.detect else r"\bSAP\b|S/4|S4HANA|\bECC\b|\bBTP\b|Integration Suite|\bCPI\b|PI/PO|Cloud Connector|IDoc|\bRFC\b|OData", re.IGNORECASE)
ICON_PAD = Inches(0.06)
ICON_MAX = Inches(0.45)
ICON_MIN = Inches(0.16)
ICON_BADGE = Inches(0.18)  # corner badge for nodes too narrow to hold an icon next to the label
LABEL_MIN_WIDTH = Inches(0.8)

NODES_PER_SQ_IN = 3.5
SMALL_AREA_SQ_IN = 20.0
SUBTITLE_MIN_HEIGHT = Inches(0.55)
MARK_WIDTH = Inches(0.4)
LAYOUTS = ("bands", "columns", "sequence")
SEQUENCE_MAX_NODES = 4
EMU_PER_PT = 12700
LABEL_SIZES = (11.0, 10.0, 9.0, 8.0, 7.0)
MIN_LABEL_PT = 6.0
WRAP_SLACK = 0.88  # PowerPoint wraps a rounded box earlier than its box width suggests
SEQUENCE_MIN_BUNDLE = 3  # this many edges between one pair of nodes read better as a sequence

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
                                "subtitle": {"type": "string"},
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
                                "kind": {"type": "string", "enum": ["sync", "async", "file", "error"]},
                            },
                            "required": ["source", "target"],
                        },
                    },
                    "notes": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "lanes": {"type": "object", "properties": {lane: {"type": "string"} for lane in LANES}},
                },
                "required": ["section", "nodes", "edges"],
            },
        }
    },
    "required": ["flows"],
}

SYSTEM_PROMPT = """You design integration flow diagrams for a solution-design document.
For each requested diagram return its nodes, edges and lane headings.
Nodes: four to nine, fewer when the diagram line caps them, each with a short label (at most
four words), a subtitle (at most four words naming the edition, deployment or role, such as
"Outbound-only tunnel" or "Backend system"; empty when nothing useful), a kind (system, step,
store or external) and a lane. When a lane list is given, a lane is the system that hosts
the node: use the id of that system. A step goes in the lane of the system that runs it: an
integration-flow step belongs to the integration platform even when it moves files on another
system; people and parties that are not systems go in "other"; never put a node named after
one system into another system's lane. Without a lane list, the lane follows the direction of
the data: source, middleware or target. Small drawing areas get at most two lanes and few nodes.
Lanes: a heading of at most five words for every lane used, naming whose landscape it is: the
customer's system landscape, the integration platform, the partner or provider side.
Edges: from node to node in flow order, each with a short label (at most three words) naming
the protocol, format or trigger, and a kind: sync, async, file or error. When the brief
describes an exception path (an alert, a watchdog, a reject folder), draw it: one node for the
alerting service or the rejected location in the lane that owns it, reached by error edges
labelled with the failure.
Steps: three to eight numbered sentences a developer reads next to the diagram, one per edge in
flow order: what is sent, over what, and what happens when it fails where the brief says so.
""" + GROUNDING_RULE + """
Never example names. Attached pictures are the author's own diagrams: keep their block names
and arrows.
Reference: when reference architectures are listed, give each diagram the id of the closest one
(or an empty string) and name the blocks the way the listed reference diagrams do where it fits.
Return only JSON matching the schema."""


class FlowLane(BaseModel):
    """A lane of the drawing: one system of the landscape, or the role lanes when no systems are listed."""

    id: str
    title: str
    role: LaneRole = "middleware"
    sap: bool = False
    change: str = ""  # keep, change or new, as the systems fact says


OTHER_LANE = FlowLane(id="other", title="Other parties", role="other")


class FlowNode(BaseModel):
    id: str
    label: str
    kind: NodeKind = "system"
    lane: str = "middleware"  # a system id from the lane list, else source, middleware or target
    icon: str = ""  # a key of the icon catalogue, empty for a plain shape
    subtitle: str = ""  # a second line: edition, deployment or role


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
    steps: list[str] = Field(default_factory=list)  # what happens along the edges, in order
    lanes: dict[str, str] = Field(default_factory=dict)  # a heading per lane, else the generic lane title
    systems: list[FlowLane] = Field(default_factory=list)  # the lanes in drawing order; empty means the three role lanes
    reference: str = ""  # "<system>:<id>" of the closest reference architecture, empty when none
    layout: Literal["bands", "columns", "sequence"] = "bands"  # system bands left to right, vertical lane columns, or participants with numbered arrows

    def save(self, path: str | Path) -> None:
        Path(path).write_text(yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> FlowSpec:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def uses_sap(brief: Brief) -> bool:
    """Whether the brief talks about an SAP landscape, which turns the SAP icon set on."""
    return bool(SAP_RE.search(dump_brief(brief)))


def parse_systems(text: str) -> list[FlowLane]:
    """One lane per line of the systems fact: system | role | keep, change or new; sorted by role, ids unique."""
    lanes: list[FlowLane] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        if not line:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        name = " ".join(cells[0].split())
        if not name:
            continue
        base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "system"
        lane_id, number = base, 2
        while lane_id in seen:
            lane_id, number = f"{base}_{number}", number + 1
        seen.add(lane_id)
        lanes.append(FlowLane(id=lane_id, title=name, role=_role_of(cells[1] if len(cells) > 1 else ""), sap=bool(SAP_RE.search(name)), change=cells[2] if len(cells) > 2 else ""))
    return sorted(lanes, key=lambda lane: ROLE_ORDER[lane.role])


def system_lanes(brief: Brief) -> list[FlowLane]:
    return parse_systems(brief.facts.get("systems", ""))


def lane_order(spec: FlowSpec) -> list[str]:
    """The lane ids in drawing order: the systems of the spec, else the three role lanes."""
    return [lane.id for lane in spec.systems] or list(LANES)


def lane_title(spec: FlowSpec, lane: str) -> str:
    """The lane's heading from the model, else the system name, else the generic one."""
    system = next((s for s in spec.systems if s.id == lane), None)
    return spec.lanes.get(lane) or (system.title if system else "") or LANE_TITLES.get(lane, lane)


def lane_mismatches(spec: FlowSpec) -> list[str]:
    """Nodes that name other systems than the one whose lane they sit in."""
    found = []
    for node in spec.nodes:
        mentioned = _mentioned_lanes(node, spec.systems)
        if mentioned and node.lane not in mentioned:
            names = ", ".join(lane_title(spec, lane) for lane in mentioned)
            found.append(f"'{node.label}' sits in {lane_title(spec, node.lane)} but names {names}")
    return found


def flow_schema(lane_ids: list[str]) -> dict:
    import copy

    schema = copy.deepcopy(FLOW_SCHEMA)
    flow = schema["properties"]["flows"]["items"]["properties"]
    flow["nodes"]["items"]["properties"]["lane"] = {"type": "string", "enum": list(lane_ids)}
    flow["lanes"] = {"type": "object", "properties": {lane: {"type": "string"} for lane in lane_ids}}
    return schema


def _role_of(text: str) -> LaneRole:
    lowered = text.lower()
    for role, words in ROLE_WORDS.items():
        if any(word in lowered for word in words):
            return role  # type: ignore[return-value]
    return "middleware"


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower().replace("/", "")) if len(t) >= 2 and t not in STOP_TOKENS]


def _mentions(node: FlowNode, lane: FlowLane) -> bool:
    system = _tokens(lane.title)
    return any(token == word or (len(token) >= 2 and word.startswith(token)) for token in _tokens(f"{node.label} {node.subtitle}") for word in system)


def _mentioned_lanes(node: FlowNode, lanes: list[FlowLane]) -> list[str]:
    return [lane.id for lane in lanes if lane.role != "other" and _mentions(node, lane)]


def _repair_lane(node: FlowNode, lanes: list[FlowLane], known: list[str]) -> str:
    """The node's lane from the list; a node named after exactly one other system moves there."""
    mentioned = _mentioned_lanes(node, lanes)
    if node.lane in known and (node.lane in mentioned or len(mentioned) != 1):
        return node.lane
    if len(mentioned) == 1:
        return mentioned[0]
    if node.lane in known:
        return node.lane
    if node.kind == "external":
        return OTHER_LANE.id
    return next((lane.id for lane in lanes if lane.role == "middleware"), lanes[0].id if lanes else OTHER_LANE.id)


def node_is_sap(node: FlowNode) -> bool:
    """Whether a node draws in the SAP look: its icon decides, else the words of its label."""
    icon = catalogue().get(node.icon) if node.icon else None
    if icon is not None:
        return icon.sap
    return bool(SAP_RE.search(f"{node.label} {node.subtitle}"))


def lane_is_sap(spec: FlowSpec, lane: str) -> bool:
    """A lane draws in the SAP look when its system or heading says so; role lanes follow their nodes' majority."""
    if SAP_RE.search(spec.lanes.get(lane, "")):
        return True
    system = next((s for s in spec.systems if s.id == lane), None)
    if system is not None:
        return system.sap
    members = [n for n in spec.nodes if n.lane == lane]
    sap = sum(node_is_sap(n) for n in members)
    return sap > 0 and sap >= len(members) - sap


def plan_flows(brief: Brief, requests: list, llm: LLMClient, icons: list[str] | None = None, images: list[tuple[bytes, str]] | None = None) -> dict[str, FlowSpec]:
    """One model call for all requested diagrams; each request has section, title and purpose.

    With `icons`, every node also gets the closest key of that catalogue. With `images`, the author's
    pictures (bytes, mime) travel with the call for models that can look at them.
    """
    if not requests:
        return {}
    lines = ["# Diagrams to design (section key | title | purpose | drawing area)"]
    for request in requests:
        hint = _area_hint(request) + (" | a sequence: two to four participants and every exchange between them as an edge, in order" if getattr(request, "kind", "flow") == "sequence" else "")
        lines.append(f"- {request.section} | {request.title or request.section} | {request.purpose or ''} | {hint}")
    systems = system_lanes(brief)
    schema = flow_schema([lane.id for lane in systems] + [OTHER_LANE.id]) if systems else FLOW_SCHEMA
    if systems:
        lines += ["", "# Lanes: one per system, use the id (id | system | role | keep, change or new)"]
        lines += [f"- {lane.id} | {lane.title} | {lane.role} | {lane.change}" for lane in systems]
        lines.append(f"- {OTHER_LANE.id} | people or parties that are not a system | other |")
    if icons:
        schema = _schema_with_icons(schema, icons)
        known = catalogue()
        lines += ["", "# Icon keys: give every node the closest one; SAP systems take SAP keys, other systems the non-SAP ones", "; ".join(f"{k} = {known[k].label}" if k in known else k for k in icons)]
    packs = matching(brief.facts_text() + "\n" + dump_brief(brief))
    if packs:
        schema = _schema_with_references(schema, packs)
        lines += _reference_lines(packs, brief.facts_text() + "\n" + dump_brief(brief))
    lines += ["", "# Facts", brief.facts_text() or "(none)"]
    block = material_text(brief.material, tags={request.section for request in requests})
    if block:
        lines += ["", "# Reference material (the author's own diagrams and notes; keep their block names and arrows)", block]
    lines += ["", "# Brief", dump_brief(brief)]
    data = llm.complete_json(SYSTEM_PROMPT, "\n".join(lines), schema, name="flows", **({"images": images} if images else {}))
    wanted = {request.section: getattr(request, "kind", "flow") for request in requests}
    result: dict[str, FlowSpec] = {}
    for item in data.get("flows") or []:
        key = str(item.get("section") or "")
        if key not in wanted:
            continue
        try:
            spec = FlowSpec.model_validate(_payload(item))
        except ValidationError:
            continue
        spec = clean_flow(spec, systems or None)
        if wanted[key] == "sequence" or looks_like_sequence(spec):
            spec = spec.model_copy(update={"layout": "sequence"})
        if spec.nodes:
            result[key] = spec
    return result


def looks_like_sequence(spec: FlowSpec) -> bool:
    """A few participants exchanging several messages: a handshake, not a flow."""
    if not spec.nodes or len(spec.nodes) > SEQUENCE_MAX_NODES:
        return False
    pairs: dict[frozenset, int] = {}
    for edge in spec.edges:
        if edge.source != edge.target:
            pairs[frozenset((edge.source, edge.target))] = pairs.get(frozenset((edge.source, edge.target)), 0) + 1
    return max(pairs.values(), default=0) >= SEQUENCE_MIN_BUNDLE


def node_cap(width_in: float, height_in: float) -> int:
    """Nodes a drawing area can show readably."""
    return max(4, min(9, int(width_in * height_in / NODES_PER_SQ_IN)))


def _payload(item: dict) -> dict:
    """The flow fields of one model item with safe defaults, so an odd or missing field cannot sink the flow."""
    lanes = item.get("lanes")
    return {
        "title": item.get("title") or "",
        "nodes": item.get("nodes") or [],
        "edges": item.get("edges") or [],
        "notes": item.get("notes") or "",
        "steps": item.get("steps") or [],
        "lanes": {str(k): str(v) for k, v in lanes.items()} if isinstance(lanes, dict) else {},
        "reference": str(item.get("reference") or ""),
    }


def _area_hint(request) -> str:
    width, height = float(getattr(request, "width_in", 0) or 0), float(getattr(request, "height_in", 0) or 0)
    if not width or not height:
        return "size unknown, at most 9 nodes"
    hint = f"drawing area {width:.1f} x {height:.1f} in, at most {node_cap(width, height)} nodes"
    return hint + (", two lanes at most" if width * height < SMALL_AREA_SQ_IN else "")


def _schema_with_icons(schema: dict, icons: list[str]) -> dict:
    import copy

    schema = copy.deepcopy(schema)
    schema["properties"]["flows"]["items"]["properties"]["nodes"]["items"]["properties"]["icon"] = {"type": "string", "enum": list(icons)}
    return schema


def _schema_with_references(schema: dict, packs: list[Pack]) -> dict:
    import copy

    schema = copy.deepcopy(schema)
    ids = [p.qualified(r) for p in packs for r in p.entries]
    schema["properties"]["flows"]["items"]["properties"]["reference"] = {"type": "string", "enum": ids + [""]}
    return schema


def _reference_lines(packs: list[Pack], text: str) -> list[str]:
    """The catalogue of every matching pack and, when fetched, the block names of its closest reference diagrams."""
    lines: list[str] = []
    for found in packs:
        lines += ["", f"# {found.name} reference architectures: give every diagram the id of the closest one, or empty"]
        lines += [f"- {found.qualified(r)} | {r.title} | {r.summary}" for r in found.entries]
        named = [(r, labels(found.system, r.id)) for r in candidates(found, text)]
        named = [(r, words) for r, words in named if words]
        if named:
            lines += ["", f"# How {found.name} names the blocks in the closest reference diagrams"]
            lines += [f"- {r.title}: " + "; ".join(words) for r, words in named]
    return lines


def clean_flow(spec: FlowSpec, lanes: list[FlowLane] | None = None) -> FlowSpec:
    """Drops what cannot be drawn and, with a lane list, puts every node in a lane of that list."""
    seen: set[str] = set()
    known = set(icon_keys())
    lanes = list(lanes or spec.systems)
    lane_ids = [lane.id for lane in lanes] + [OTHER_LANE.id] if lanes else list(LANES)
    nodes = []
    for node in spec.nodes:
        node_id = _ident(node.id)
        if not node_id or node_id in seen or not node.label.strip():
            continue
        seen.add(node_id)
        lane = _repair_lane(node, lanes, lane_ids) if lanes else (node.lane if node.lane in lane_ids else "middleware")
        nodes.append(node.model_copy(update={"id": node_id, "label": " ".join(node.label.split()), "subtitle": " ".join(node.subtitle.split()), "icon": node.icon if node.icon in known else "", "lane": lane}))
    edges = []
    for edge in spec.edges:
        source, target = _ident(edge.source), _ident(edge.target)
        if source in seen and target in seen and source != target:
            edges.append(edge.model_copy(update={"source": source, "target": target, "label": " ".join(edge.label.split())}))
    steps = [" ".join(str(s).split()) for s in spec.steps if str(s).strip()]
    headings = {lane: " ".join(str(title).split()) for lane, title in spec.lanes.items() if lane in lane_ids and str(title).strip()}
    used = list(lanes)  # every listed system, so checks still know the lanes a node does not sit in
    if lanes and any(n.lane == OTHER_LANE.id for n in nodes):
        used.append(OTHER_LANE)
    reference = spec.reference.strip() if lookup(spec.reference.strip()) else ""
    return spec.model_copy(update={"nodes": nodes, "edges": edges, "steps": steps, "lanes": headings, "systems": used, "reference": reference})


def flow_steps(spec: FlowSpec) -> list[str]:
    """The model's steps, else one sentence per edge in order."""
    stored = [s.strip() for s in spec.steps if s.strip()]
    if stored:
        return stored
    labels = {n.id: n.label for n in spec.nodes}
    steps = []
    for edge in spec.edges:
        if edge.source not in labels or edge.target not in labels:
            continue
        steps.append(f"{labels[edge.source]} to {labels[edge.target]}" + (f": {edge.label}." if edge.label else "."))
    return steps


def walkthrough_text(spec: FlowSpec) -> str:
    return "\n".join(f"{number}. {step}" for number, step in enumerate(flow_steps(spec), 1))


def to_mermaid(spec: FlowSpec) -> str:
    lines = ["flowchart LR"]
    for lane in lane_order(spec):
        members = [n for n in spec.nodes if n.lane == lane]
        if not members:
            continue
        lines.append(f"  subgraph {lane}[{_mermaid_title(lane_title(spec, lane))}]")
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
    arrows = {"sync": "-->", "async": "-.->", "file": "==>", "error": "-.->"}
    for edge in spec.edges:
        arrow = arrows.get(edge.kind, "-->")
        label = f"|{edge.label}|" if edge.label else ""
        lines.append(f"  {edge.source} {arrow}{label} {edge.target}")
    return "\n".join(lines) + "\n"


def layout_for(spec: FlowSpec, box: tuple[int, int, int, int], mode: str | None = None) -> FlowLayout:
    """The positions of the spec's lanes, nodes and edges inside the box, in the spec's layout unless `mode` says otherwise."""
    lanes = [LaneInfo(id=lane, title=lane_title(spec, lane), sap=lane_is_sap(spec, lane)) for lane in lane_order(spec)]
    nodes = [NodeIn(id=n.id, lane=n.lane, label=n.label, subtitle=n.subtitle) for n in spec.nodes]
    edges = [EdgeIn(number=number, source=e.source, target=e.target, label=e.label, kind=e.kind) for number, e in enumerate(spec.edges, 1)]
    return layout_flow(nodes, edges, lanes, box, mode or spec.layout)


def draw_flow(slide, box: tuple[int, int, int, int], spec: FlowSpec, prefix: str = "Flow", mode: str | None = None) -> list:
    """Draws the flow as editable shapes inside the box (EMU left, top, width, height)."""
    layout = layout_for(spec, box, mode)
    created = [_canvas(slide, *box, f"{prefix} canvas")]
    for band in layout.lanes:
        name = f"{prefix} lane {band.lane}" + (f" row {band.row + 1}" if band.row else "")
        created.extend(_lane_box(slide, band.rect.left, band.rect.top, band.rect.width, band.rect.height, band.title, band.sap, name))
    shapes: dict[str, object] = {}
    for node in spec.nodes:
        placed = layout.nodes.get(node.id)
        if placed is None:
            continue
        shape = _node(slide, node, placed.rect.left, placed.rect.top, placed.rect.width, placed.rect.height, prefix, created)
        shapes[node.id] = shape
        created.append(shape)
    for node_id, x, top, bottom in layout.lifelines:
        created.append(_lifeline(slide, x, top, bottom, f"{prefix} lifeline {node_id}"))
    for path in layout.edges:
        edge = spec.edges[path.number - 1]
        name = f"{prefix} edge {path.number}"
        if path.sites is not None:
            created.append(_connector(slide, shapes[path.source], shapes[path.target], path.sites, path.points, edge, name))
        else:
            created.append(_polyline(slide, path.points, edge, name))
        if path.label is not None:
            created.append(_draw_label(slide, path.label, name))
    return created


def _canvas(slide, x: int, y: int, width: int, height: int, name: str):
    # A white board behind the diagram keeps it readable on dark slide backgrounds.
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, width, height)
    shape.name = name
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(WHITE)
    shape.line.fill.background()
    shape.shadow.inherit = False
    shape.text_frame.text = ""
    return shape


def _lane_box(slide, x: int, y: int, width: int, height: int, title: str, sap: bool, name: str) -> list:
    """The tinted rounded container of a lane with its heading top-left; SAP lanes carry the SAP word mark before it."""
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, width, height)
    shape.name = name
    shape.adjustments[0] = 0.05
    shape.shadow.inherit = False
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(SAP_FILL if sap else GREY_FILL)
    shape.line.color.rgb = rgb(SAP_BLUE if sap else SLATE)
    shape.line.width = Pt(1.5)
    frame = shape.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.TOP
    frame.margin_left = PAD + (MARK_WIDTH if sap and title else 0)
    frame.margin_top = Inches(0.06)
    paragraph = frame.paragraphs[0]
    paragraph.text = title
    paragraph.alignment = PP_ALIGN.LEFT
    heading_pt, _ = fitting_size(title, "", width - frame.margin_left - PAD, HEADER)
    for run in paragraph.runs:
        run.font.size = Pt(min(10.0, heading_pt))  # long system names still fit the header on a narrow lane
        run.font.bold = True
        run.font.color.rgb = rgb(SAP_DARK if sap else SLATE)
    created = [shape]
    if sap and title:
        created.append(_lane_mark(slide, x + PAD, y + Inches(0.07), f"{name} mark"))
    return created


def _lane_mark(slide, x: int, y: int, name: str):
    box = slide.shapes.add_textbox(x, y, MARK_WIDTH, Inches(0.2))
    box.name = name
    frame = box.text_frame
    frame.word_wrap = False
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.TOP
    paragraph = frame.paragraphs[0]
    paragraph.text = "SAP"
    paragraph.alignment = PP_ALIGN.LEFT
    for run in paragraph.runs:
        run.font.size = Pt(9)
        run.font.bold = True
        run.font.color.rgb = rgb(SAP_BLUE)
    return box


def _node(slide, node: FlowNode, x: int, y: int, width: int, height: int, prefix: str, created: list | None = None):
    sap = node_is_sap(node)
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, width, height)
    shape.name = f"{prefix} node {node.id}"
    shape.adjustments[0] = 0.2
    shape.shadow.inherit = False
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(WHITE if sap else GREY_FILL)
    shape.line.color.rgb = rgb(SAP_BLUE if sap else SLATE)
    shape.line.width = Pt(1.5)
    if not sap:
        shape.line.dash_style = MSO_LINE.ROUND_DOT
    frame = shape.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    frame.margin_left = frame.margin_right = Inches(0.05)
    frame.margin_top = frame.margin_bottom = Inches(0.03)
    png = icon_png(node.icon) if node.icon else None
    if png is not None:
        room = min(ICON_MAX, height - 2 * ICON_PAD, width - LABEL_MIN_WIDTH - 2 * ICON_PAD)
        if room >= ICON_MIN:
            picture = slide.shapes.add_picture(str(png), x + ICON_PAD, y + (height - room) // 2, room, room)
            frame.margin_left = room + 2 * ICON_PAD
        else:
            picture = slide.shapes.add_picture(str(png), x + ICON_PAD // 2, y + ICON_PAD // 2, ICON_BADGE, ICON_BADGE)
        picture.name = f"{prefix} icon {node.id}"
        if created is not None:
            created.append(picture)
    subtitle = node.subtitle if node.subtitle and height >= SUBTITLE_MIN_HEIGHT else ""
    label_pt, subtitle_pt = fitting_size(node.label, subtitle, width - frame.margin_left - frame.margin_right, height - frame.margin_top - frame.margin_bottom)
    paragraph = frame.paragraphs[0]
    paragraph.text = node.label
    paragraph.alignment = PP_ALIGN.CENTER
    for run in paragraph.runs:
        run.font.size = Pt(label_pt)
        run.font.bold = True
        run.font.color.rgb = rgb(TEXT)
    if subtitle:
        second = frame.add_paragraph()
        second.text = subtitle
        second.alignment = PP_ALIGN.CENTER
        for run in second.runs:
            run.font.size = Pt(subtitle_pt)
            run.font.color.rgb = rgb(SUBTITLE)
    return shape


def fitting_size(label: str, subtitle: str, width: int, height: int) -> tuple[float, float]:
    """Largest of the label sizes whose longest word fits the width and whose lines fit the height."""
    width_pt, height_pt = max(width, 1) / EMU_PER_PT * WRAP_SLACK, max(height, 1) / EMU_PER_PT
    for size in LABEL_SIZES:
        small = max(MIN_LABEL_PT, size - 3)
        spec = FontSpec(size_pt=size, bold=True)
        longest = max((text_width_pt(word, spec) for word in label.split()), default=0.0)
        needed = wrapped_lines(label, width_pt, spec) * line_height_pt(spec)
        if subtitle:
            sub = FontSpec(size_pt=small)
            needed += wrapped_lines(subtitle, width_pt, sub) * line_height_pt(sub)
        if longest <= width_pt and needed <= height_pt:
            return size, small
    return MIN_LABEL_PT, MIN_LABEL_PT


def _connector(slide, shape_a, shape_b, sites: tuple[int, int], points: list[tuple[int, int]], edge: FlowEdge, name: str):
    """A straight connector glued to both nodes, so it follows them when a reader moves a box."""
    site_a, site_b = sites
    (x1, y1), (x2, y2) = points[0], points[-1]
    connector = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT if len(points) == 2 else MSO_CONNECTOR.ELBOW, x1, y1, x2, y2)
    connector.name = name
    connector.begin_connect(shape_a, site_a)
    connector.end_connect(shape_b, site_b)
    _style_line(connector, edge)
    return connector


def _lifeline(slide, x: int, top: int, bottom: int, name: str):
    """The thin dashed line under a participant of a sequence."""
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x, top, x, bottom)
    line.name = name
    line.line.color.rgb = rgb(SLATE)
    line.line.width = Pt(0.75)
    line.line.dash_style = MSO_LINE.DASH
    return line


def _polyline(slide, points: list[tuple[int, int]], edge: FlowEdge, name: str):
    builder = slide.shapes.build_freeform(points[0][0], points[0][1], scale=1.0)
    builder.add_line_segments(points[1:], close=False)
    shape = builder.convert_to_shape()
    shape.name = name
    shape.fill.background()
    _style_line(shape, edge)
    return shape


def _style_line(shape, edge: FlowEdge) -> None:
    shape.line.color.rgb = rgb(EDGE)
    shape.line.width = Pt(1.5)
    if edge.kind != "sync":
        shape.line.dash_style = MSO_LINE.DASH
    _arrow_head(shape)


def _draw_label(slide, label: LabelBox, name: str):
    """A white label box where the layout put it."""
    box = slide.shapes.add_textbox(label.rect.left, label.rect.top, label.rect.width, label.rect.height)
    box.rotation = label.rotation
    box.name = f"{name} label"
    box.fill.solid()
    box.fill.fore_color.rgb = rgb(WHITE)
    box.line.fill.background()
    frame = box.text_frame
    frame.word_wrap = False
    frame.margin_left = frame.margin_right = Inches(0.03)
    frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]
    paragraph.text = label.text
    paragraph.alignment = PP_ALIGN.CENTER
    for run in paragraph.runs:
        run.font.size = Pt(label.font_pt)
        run.font.color.rgb = rgb(SUBTITLE)
    return box


def _arrow_head(connector) -> None:
    ln = connector.line._get_or_add_ln()
    tail = ln.find(qn("a:tailEnd"))
    if tail is None:
        tail = etree.SubElement(ln, qn("a:tailEnd"))
    tail.set("type", "triangle")


def _ident(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).strip("_")


def _mermaid_title(text: str) -> str:
    # Brackets and other punctuation inside a subgraph title need the quoted form.
    return text if re.fullmatch(r"[A-Za-z0-9 _-]+", text) else '"' + text.replace('"', "'") + '"'
