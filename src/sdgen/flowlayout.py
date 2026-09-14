"""Where the boxes and lines of a flow go: a pure layout in EMU shared by the slide drawing and the draw.io export.

Bands: one horizontal band per lane, nodes placed left to right by their rank along the edges, nodes shrink
first and the diagram then wraps like a music score (every band again below, the last node of a row linked
to the first of the next). Columns: the vertical lane containers with stacked nodes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pptx.util import Inches

from sdgen.textmetrics import FontSpec, text_width_pt

TOP, LEFT, BOTTOM, RIGHT = 0, 1, 2, 3
NODE_HEIGHT = Inches(0.7)
NODE_WIDTH = Inches(3.0)
MIN_NODE = Inches(0.4)
BAND_MIN_NODE_W = Inches(1.1)  # narrower than this and the diagram wraps instead
NARROW_NODE_W = Inches(0.6)  # the floor when wrapping cannot help either
GAP = Inches(0.35)
HEADER = Inches(0.35)
PAD = Inches(0.15)
LANE_GAP = Inches(0.3)  # white seam between lane containers, where routed edges travel
ROW_GAP = Inches(0.45)  # corridor between two rows of bands
CHANNEL = Inches(0.45)
NODE_INSET = Inches(0.25)
BAND_MIN_H = HEADER + Inches(0.55) + 2 * PAD  # room for a heading and a node with its subtitle
ROUTE_OFFSET = Inches(0.2)
ROUTE_STEP = Inches(0.3)
SEAM_STEP = Inches(0.1)
SEAM_INSET = Inches(0.1)
PARALLEL_STEP = Inches(0.12)
LABEL_HEIGHT = Inches(0.22)
LABEL_PAD = Inches(0.12)
LABEL_LIFT = Inches(0.03)
LABEL_FONT = FontSpec("Arial", 8.0, False)
LABEL_SMALL_FONT = FontSpec("Arial", 7.0, False)


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def cx(self) -> int:
        return self.left + self.width // 2

    @property
    def cy(self) -> int:
        return self.top + self.height // 2


@dataclass(frozen=True)
class LaneInfo:
    id: str
    title: str
    sap: bool = False


@dataclass(frozen=True)
class NodeIn:
    id: str
    lane: str
    label: str = ""
    subtitle: str = ""


@dataclass(frozen=True)
class EdgeIn:
    number: int
    source: str
    target: str
    label: str = ""
    kind: str = "sync"


@dataclass
class LaneBox:
    lane: str
    title: str
    sap: bool
    row: int
    rect: Rect


@dataclass
class NodeBox:
    id: str
    lane: str
    row: int
    column: int
    rect: Rect


@dataclass
class LabelBox:
    text: str
    rect: Rect
    rotation: float = 0.0
    font_pt: float = 8.0


@dataclass
class EdgePath:
    number: int
    source: str
    target: str
    kind: str
    points: list[tuple[int, int]]
    sites: tuple[int, int] | None = None  # connection sites of a glued straight connector, else a routed polyline
    label: LabelBox | None = None


@dataclass
class FlowLayout:
    mode: str
    canvas: Rect
    lanes: list[LaneBox] = field(default_factory=list)
    nodes: dict[str, NodeBox] = field(default_factory=dict)
    edges: list[EdgePath] = field(default_factory=list)
    rows: int = 0
    per_row: int = 0
    lifelines: list[tuple[str, int, int, int]] = field(default_factory=list)  # sequence mode: node id, x, top, bottom


def layout_flow(nodes: list[NodeIn], edges: list[EdgeIn], lanes: list[LaneInfo], box: tuple[int, int, int, int], mode: str = "bands") -> FlowLayout:
    """Positions for the lanes, nodes and edges inside the box (EMU left, top, width, height)."""
    canvas = Rect(*box)
    known = {lane.id for lane in lanes}
    nodes = [n if n.lane in known else NodeIn(n.id, lanes[0].id, n.label, n.subtitle) for n in nodes] if lanes else []
    used = [lane for lane in lanes if any(n.lane == lane.id for n in nodes)]
    if not nodes or not used:
        return FlowLayout(mode=mode, canvas=canvas)
    if mode == "sequence":
        return _sequence(nodes, edges, canvas)
    if mode == "bands":
        result = _bands(nodes, edges, used, canvas)
        if result is not None:
            return result
    return _columns(nodes, edges, used, canvas)


def _sequence(nodes: list[NodeIn], edges: list[EdgeIn], canvas: Rect) -> FlowLayout:
    """Participants across the top with a lifeline each; every edge a numbered horizontal arrow, top to bottom in edge order."""
    inner = Rect(canvas.left + PAD, canvas.top + PAD, canvas.width - 2 * PAD, canvas.height - 2 * PAD)
    columns = rank_columns(nodes, edges)
    order = sorted(nodes, key=lambda n: (columns[n.id], [m.id for m in nodes].index(n.id)))
    count = len(order)
    node_w = min(NODE_WIDTH, (inner.width - (count - 1) * GAP) // count)
    step = (inner.width - node_w) // (count - 1) if count > 1 else 0
    placed: dict[str, NodeBox] = {}
    lifelines: list[tuple[str, int, int, int]] = []
    for index, node in enumerate(order):
        x = inner.left + index * step if count > 1 else inner.left + (inner.width - node_w) // 2
        rect = Rect(x, inner.top, node_w, NODE_HEIGHT)
        placed[node.id] = NodeBox(id=node.id, lane=node.lane, row=0, column=index, rect=rect)
        lifelines.append((node.id, rect.cx, rect.bottom, inner.bottom))
    arrows = [e for e in edges if e.source in placed and e.target in placed and e.source != e.target]
    gap_y = (inner.height - NODE_HEIGHT - GAP) // (len(arrows) + 1)
    paths: list[EdgePath] = []
    for index, edge in enumerate(arrows, 1):
        y = inner.top + NODE_HEIGHT + GAP + index * gap_y
        points = [(placed[edge.source].rect.cx, y), (placed[edge.target].rect.cx, y)]
        text = f"{index}. {edge.label}" if edge.label else str(index)
        paths.append(EdgePath(number=edge.number, source=edge.source, target=edge.target, kind=edge.kind, points=points, sites=None, label=label_for(points, text, "above")))
    return FlowLayout(mode="sequence", canvas=canvas, nodes=placed, edges=paths, rows=1, per_row=count, lifelines=lifelines)


def rank_columns(nodes: list[NodeIn], edges: list[EdgeIn]) -> dict[str, int]:
    """A column per node: after its predecessors, unique inside its lane; edges that would close a cycle do not count."""
    ids = [n.id for n in nodes]
    index = {node_id: position for position, node_id in enumerate(ids)}
    lane = {n.id: n.lane for n in nodes}
    successors: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in edges:
        if edge.source not in index or edge.target not in index or edge.source == edge.target:
            continue
        if edge.target in successors[edge.source] or _reaches(edge.target, edge.source, successors):
            continue
        successors[edge.source].append(edge.target)
    predecessors: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for source, targets in successors.items():
        for target in targets:
            predecessors[target].append(source)
    pending = {node_id: len(predecessors[node_id]) for node_id in ids}
    ready = [node_id for node_id in ids if pending[node_id] == 0]
    order: list[str] = []
    while ready:
        ready.sort(key=index.get)
        current = ready.pop(0)
        order.append(current)
        for target in successors[current]:
            pending[target] -= 1
            if pending[target] == 0:
                ready.append(target)
    columns: dict[str, int] = {}
    next_free: dict[str, int] = {}
    for node_id in order:
        column = max([next_free.get(lane[node_id], 0)] + [columns[p] + 1 for p in predecessors[node_id]])
        columns[node_id] = column
        next_free[lane[node_id]] = column + 1
    return columns


def _reaches(start: str, goal: str, successors: dict[str, list[str]]) -> bool:
    stack, seen = [start], set()
    while stack:
        current = stack.pop()
        if current == goal:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(successors.get(current, []))
    return False


def _bands(nodes: list[NodeIn], edges: list[EdgeIn], lanes: list[LaneInfo], canvas: Rect) -> FlowLayout | None:
    columns = rank_columns(nodes, edges)
    count = max(columns.values()) + 1
    lane_index = {lane.id: k for k, lane in enumerate(lanes)}
    lane_of = {n.id: n.lane for n in nodes}
    far = any(abs(lane_index[lane_of[e.source]] - lane_index[lane_of[e.target]]) >= 2 for e in edges if e.source in lane_of and e.target in lane_of)
    inner = Rect(canvas.left + PAD, canvas.top + PAD, canvas.width - 2 * PAD, canvas.height - 2 * PAD)
    depth = len(lanes)

    def usable(rows: int) -> int:
        return inner.width - 2 * PAD - (CHANNEL if rows > 1 else 0) - (CHANNEL if far or rows > 1 else 0)

    def width_for(rows: int, per_row: int) -> int:
        return min(NODE_WIDTH, (usable(rows) - (per_row - 1) * GAP) // per_row)

    def band_height(rows: int) -> int:
        return (inner.height - (rows - 1) * ROW_GAP) // (rows * depth)

    rows, per_row = 1, count
    node_w = width_for(1, count)
    if node_w < BAND_MIN_NODE_W and count > 1:
        per_row = max(1, int((usable(2) + GAP) // (BAND_MIN_NODE_W + GAP)))
        rows = math.ceil(count / per_row)
        while rows > 1 and band_height(rows) < BAND_MIN_H:
            rows -= 1
            per_row = math.ceil(count / rows)
        node_w = width_for(rows, per_row)
    band_h = band_height(rows)
    if node_w < NARROW_NODE_W or band_h < BAND_MIN_H:
        return None  # too many columns or too flat for bands: columns keep every node readable
    node_h = max(MIN_NODE, min(NODE_HEIGHT, band_h - LANE_GAP - HEADER - 2 * PAD))
    left_channel = CHANNEL if rows > 1 else 0
    right_channel = CHANNEL if far or rows > 1 else 0

    lane_boxes: list[LaneBox] = []
    bands: dict[tuple[int, int], Rect] = {}
    for row in range(rows):
        for k, lane in enumerate(lanes):
            top = inner.top + row * (depth * band_h + ROW_GAP) + k * band_h + LANE_GAP // 2
            rect = Rect(inner.left + left_channel, top, inner.width - left_channel - right_channel, band_h - LANE_GAP)
            bands[(row, k)] = rect
            lane_boxes.append(LaneBox(lane=lane.id, title=lane.title, sap=lane.sap, row=row, rect=rect))
    placed: dict[str, NodeBox] = {}
    for node in nodes:
        row, column = divmod(columns[node.id], per_row)
        band = bands[(row, lane_index[node.lane])]
        x = band.left + PAD + column * (node_w + GAP)
        y = band.top + HEADER + (band.height - HEADER - node_h) // 2
        placed[node.id] = NodeBox(id=node.id, lane=node.lane, row=row, column=column, rect=Rect(x, y, node_w, node_h))
    geometry = {
        "lane_index": lane_index,
        "bands": bands,
        "depth": depth,
        "band_h": band_h,
        "inner": inner,
        "left_x": inner.left + left_channel // 2,
        "right_x": inner.right - right_channel // 2,
    }
    paths = _route_bands(edges, placed, geometry)
    return FlowLayout(mode="bands", canvas=canvas, lanes=lane_boxes, nodes=placed, edges=paths, rows=rows, per_row=per_row)


def _route_bands(edges: list[EdgeIn], placed: dict[str, NodeBox], geometry: dict) -> list[EdgePath]:
    lane_index, bands = geometry["lane_index"], geometry["bands"]
    seams: dict[tuple, int] = {}
    corridors: dict[int, int] = {}
    groups: dict[frozenset, list[int]] = {}
    for edge in edges:
        if edge.source in placed and edge.target in placed and edge.source != edge.target:
            groups.setdefault(frozenset((edge.source, edge.target)), []).append(edge.number)
    stacked: dict[tuple, int] = {}

    def seam(row: int, k: int, side: str) -> int:
        band = bands[(row, k)]
        used = seams.get((row, k, side), 0)
        seams[(row, k, side)] = used + 1
        return band.bottom + SEAM_INSET + used * SEAM_STEP if side == "below" else band.top - SEAM_INSET - used * SEAM_STEP

    def corridor(row: int) -> int:
        used = corridors.get(row, 0)
        corridors[row] = used + 1
        return geometry["inner"].top + row * (geometry["depth"] * geometry["band_h"] + ROW_GAP) - ROW_GAP // 2 + (used - 1) * SEAM_STEP

    def edge_of(node: NodeBox, side: str) -> bool:
        same = [other for other in placed.values() if other.lane == node.lane and other.row == node.row]
        return node.column == (max(o.column for o in same) if side == "right" else min(o.column for o in same))

    by_number = {edge.number: edge for edge in edges}
    paths: list[EdgePath] = []
    for edge in edges:
        a, b = placed.get(edge.source), placed.get(edge.target)
        if a is None or b is None or a is b:
            continue
        group = groups[frozenset((edge.source, edge.target))]
        n, j = len(group), group.index(edge.number)
        offset = int((j - (n - 1) / 2) * min(PARALLEL_STEP, a.rect.height // (n + 1))) if n > 1 else 0
        ka, kb = lane_index[a.lane], lane_index[b.lane]
        ra, rb = a.rect, b.rect
        sites: tuple[int, int] | None = None
        placement = "on"
        lift: int | None = None
        label: LabelBox | None = None
        if a.row == b.row and ka == kb and abs(a.column - b.column) == 1:
            forward = b.column > a.column
            if n == 1:
                sites = (RIGHT, LEFT) if forward else (LEFT, RIGHT)
                points = [(ra.right, ra.cy), (rb.left, rb.cy)] if forward else [(ra.left, ra.cy), (rb.right, rb.cy)]
                lift = min(ra.top, rb.top)
            else:
                y = ra.cy + offset
                points = [(ra.right, y), (rb.left, y)] if forward else [(ra.left, y), (rb.right, y)]
                key = (frozenset((edge.source, edge.target)), forward)
                level = stacked.get(key, 0)
                stacked[key] = level + 1
                placement = ("stack-above", level) if forward else ("stack-below", level)  # type: ignore[assignment]
        elif a.row == b.row and ka == kb:
            y = seam(a.row, ka, "below")
            points = [(ra.cx, ra.bottom), (ra.cx, y), (rb.cx, y), (rb.cx, rb.bottom)]
            placement = "below"
        elif a.row == b.row and abs(ka - kb) == 1:
            down = kb > ka
            upper, lower = (a, b) if down else (b, a)
            upper_band, lower_band = bands[(upper.row, lane_index[upper.lane])], bands[(lower.row, lane_index[lower.lane])]
            y = upper_band.bottom + LANE_GAP * (j + 1) // (n + 1) if n > 1 else seam(upper.row, lane_index[upper.lane], "below")
            xa, xb = ra.cx + offset, rb.cx + offset
            points = [(xa, ra.bottom if down else ra.top), (xa, y), (xb, y), (xb, rb.top if down else rb.bottom)]
            if n > 1:
                # parallel edges across a seam: each label sits on the run next to its source, spread along it below any heading
                same = [number for number in group if (lane_index[placed[by_number[number].target].lane] > lane_index[placed[by_number[number].source].lane]) == down]
                run_top, run_bottom = (ra.bottom, upper_band.bottom) if down else (lower_band.top + HEADER, ra.top)
                label = _run_label(edge.label, xa, run_top, run_bottom, same.index(edge.number), len(same))
                placement = "done"
        elif a.row == b.row:
            down = kb > ka
            y1 = seam(a.row, ka, "below" if down else "above")
            y2 = seam(b.row, kb, "above" if down else "below")
            x = geometry["right_x"]
            points = [(ra.cx, ra.bottom if down else ra.top), (ra.cx, y1), (x, y1), (x, y2), (rb.cx, y2), (rb.cx, rb.top if down else rb.bottom)]
        elif a.row < b.row:
            right_x, left_x = geometry["right_x"], geometry["left_x"]
            start = [(ra.right, ra.cy), (right_x, ra.cy)] if edge_of(a, "right") else [(ra.cx, ra.bottom), (ra.cx, y := seam(a.row, ka, "below")), (right_x, y)]
            y_corridor = corridor(b.row)
            end = [(left_x, rb.cy), (rb.left, rb.cy)] if edge_of(b, "left") else [(left_x, y2 := seam(b.row, kb, "above")), (rb.cx, y2), (rb.cx, rb.top)]
            points = start + [(right_x, y_corridor), (left_x, y_corridor)] + end
        else:
            right_x, left_x = geometry["right_x"], geometry["left_x"]
            start = [(ra.left, ra.cy), (left_x, ra.cy)] if edge_of(a, "left") else [(ra.cx, ra.top), (ra.cx, y := seam(a.row, ka, "above")), (left_x, y)]
            y_corridor = corridor(a.row)
            end = [(right_x, rb.cy), (rb.right, rb.cy)] if edge_of(b, "right") else [(right_x, y2 := seam(b.row, kb, "below")), (rb.cx, y2), (rb.cx, rb.bottom)]
            points = start + [(left_x, y_corridor), (right_x, y_corridor)] + end
        if isinstance(placement, tuple):
            label = _stacked_label(edge.label, ra, rb, placement)
        elif placement != "done":
            label = label_for(points, edge.label, placement, lift)
        paths.append(EdgePath(number=edge.number, source=edge.source, target=edge.target, kind=edge.kind, points=points, sites=sites, label=label))
    return paths


def _run_label(text: str, x: int, top: int, bottom: int, position: int, count: int) -> LabelBox | None:
    """The label of one edge of a bundle, centred on its own line at its share of the vertical run."""
    if not text:
        return None
    width, font = _label_size(text)
    y = top + (position + 1) * (bottom - top) // (count + 1) - LABEL_HEIGHT // 2
    return LabelBox(text=text, rect=Rect(x - width // 2, y, width, LABEL_HEIGHT), font_pt=font.size_pt)


def _stacked_label(text: str, a: Rect, b: Rect, placement: tuple) -> LabelBox | None:
    """Labels of parallel edges between two neighbours: forward ones above the nodes, backward ones below, one per line."""
    if not text:
        return None
    side, level = placement
    width, font = _label_size(text)
    x = (min(a.right, b.right) + max(a.left, b.left)) // 2 - width // 2
    if side == "stack-above":
        y = min(a.top, b.top) - LABEL_LIFT - (level + 1) * LABEL_HEIGHT
    else:
        y = max(a.bottom, b.bottom) + LABEL_LIFT + level * LABEL_HEIGHT
    return LabelBox(text=text, rect=Rect(x, y, width, LABEL_HEIGHT), font_pt=font.size_pt)


def _columns(nodes: list[NodeIn], edges: list[EdgeIn], lanes: list[LaneInfo], canvas: Rect) -> FlowLayout:
    lane_index = {lane.id: k for k, lane in enumerate(lanes)}
    lane_of = {n.id: n.lane for n in nodes}
    far = any(abs(lane_index[lane_of[e.source]] - lane_index[lane_of[e.target]]) >= 2 for e in edges if e.source in lane_of and e.target in lane_of)
    inner = Rect(canvas.left + PAD, canvas.top + PAD, canvas.width - 2 * PAD, canvas.height - 2 * PAD)
    channel = CHANNEL if far else 0
    lane_width = inner.width // len(lanes)
    box_w, box_h = lane_width - LANE_GAP, inner.height - channel
    node_w = max(NARROW_NODE_W, min(NODE_WIDTH, box_w - 2 * NODE_INSET))
    lane_boxes: list[LaneBox] = []
    placed: dict[str, NodeBox] = {}
    for k, lane in enumerate(lanes):
        x0 = inner.left + k * lane_width
        lane_boxes.append(LaneBox(lane=lane.id, title=lane.title, sap=lane.sap, row=0, rect=Rect(x0 + LANE_GAP // 2, inner.top, box_w, box_h)))
        members = [n for n in nodes if n.lane == lane.id]
        node_h, gap = _fit(len(members), box_h - HEADER - 2 * PAD, NODE_HEIGHT, GAP)
        y = inner.top + HEADER + PAD
        for position, node in enumerate(members):
            placed[node.id] = NodeBox(id=node.id, lane=node.lane, row=0, column=position, rect=Rect(x0 + (lane_width - node_w) // 2, y, node_w, node_h))
            y += node_h + gap
    geometry = {"lane_index": lane_index, "left": inner.left, "lane_width": lane_width, "lanes": len(lanes), "channel_y": inner.top + inner.height - channel // 2}
    return FlowLayout(mode="columns", canvas=canvas, lanes=lane_boxes, nodes=placed, edges=_route_columns(edges, placed, geometry), rows=1, per_row=1)


def _fit(count: int, available: int, size: int, gap: int) -> tuple[int, int]:
    if count <= 1:
        return min(size, max(MIN_NODE, available)), gap
    if count * size + (count - 1) * gap <= available:
        return size, gap
    gap = gap // 2
    return max(MIN_NODE, (available - (count - 1) * gap) // count), gap


def _route_columns(edges: list[EdgeIn], placed: dict[str, NodeBox], geometry: dict) -> list[EdgePath]:
    lane_index = geometry["lane_index"]
    routed: dict[int, int] = {}
    paths: list[EdgePath] = []
    for edge in edges:
        a, b = placed.get(edge.source), placed.get(edge.target)
        if a is None or b is None or a is b:
            continue
        ka, kb = lane_index[a.lane], lane_index[b.lane]
        ra, rb = a.rect, b.rect
        sites: tuple[int, int] | None = None
        placement, lift = "on", None
        if ka == kb and abs(a.column - b.column) == 1:
            sites = (BOTTOM, TOP) if b.column > a.column else (TOP, BOTTOM)
            points = _elbow(_site(ra, sites[0]), _site(rb, sites[1]), vertical_first=True)
            lift = min(ra.top, rb.top)
        elif abs(ka - kb) == 1:
            sites = (RIGHT, LEFT) if kb > ka else (LEFT, RIGHT)
            points = _elbow(_site(ra, sites[0]), _site(rb, sites[1]), vertical_first=False)
            lift = min(ra.top, rb.top)
        elif ka == kb:
            offset = ROUTE_OFFSET + ROUTE_STEP * routed.get(ka, 0)
            routed[ka] = routed.get(ka, 0) + 1
            x = max(ra.right, rb.right) + offset
            points = [(ra.right, ra.cy), (x, ra.cy), (x, rb.cy), (rb.right, rb.cy)]
            placement = "along"
        else:
            forward = kb > ka
            boundary = lambda i: geometry["left"] + i * geometry["lane_width"]  # noqa: E731
            y = geometry["channel_y"]
            if forward:
                x_out, x_in = boundary(ka + 1), boundary(kb)
                points = [(ra.right, ra.cy), (x_out, ra.cy), (x_out, y), (x_in, y), (x_in, rb.cy), (rb.left, rb.cy)]
            else:
                x_out, x_in = boundary(ka), boundary(kb + 1)
                points = [(ra.left, ra.cy), (x_out, ra.cy), (x_out, y), (x_in, y), (x_in, rb.cy), (rb.right, rb.cy)]
        paths.append(EdgePath(number=edge.number, source=edge.source, target=edge.target, kind=edge.kind, points=points, sites=sites, label=label_for(points, edge.label, placement, lift)))
    return paths


def _site(rect: Rect, site: int) -> tuple[int, int]:
    return {TOP: (rect.cx, rect.top), LEFT: (rect.left, rect.cy), BOTTOM: (rect.cx, rect.bottom), RIGHT: (rect.right, rect.cy)}[site]


def _elbow(start: tuple[int, int], end: tuple[int, int], vertical_first: bool) -> list[tuple[int, int]]:
    (x1, y1), (x2, y2) = start, end
    if vertical_first:
        mid = (y1 + y2) // 2
        return [(x1, y1), (x1, mid), (x2, mid), (x2, y2)] if x1 != x2 else [(x1, y1), (x2, y2)]
    mid = (x1 + x2) // 2
    return [(x1, y1), (mid, y1), (mid, y2), (x2, y2)] if y1 != y2 else [(x1, y1), (x2, y2)]


def _label_size(text: str) -> tuple[int, FontSpec]:
    font = LABEL_FONT if len(text) <= 24 else LABEL_SMALL_FONT
    return int(text_width_pt(text, font) * 12700) + LABEL_PAD, font


def label_for(points: list[tuple[int, int]], text: str, placement: str = "on", lift_above: int | None = None) -> LabelBox | None:
    """A label box on the longest segment: "on" the line, "along" a vertical line (turned upright), "below" or "above" a horizontal one;
    a straight horizontal line too short for the label moves it above the nodes when `lift_above` gives their top."""
    if not text:
        return None
    (p, q) = max(zip(points, points[1:]), key=lambda s: abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1]))
    mx, my = (p[0] + q[0]) // 2, (p[1] + q[1]) // 2
    width, font = _label_size(text)
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
    return LabelBox(text=text, rect=Rect(lx, ly, width, height), rotation=rotation, font_pt=font.size_pt)
