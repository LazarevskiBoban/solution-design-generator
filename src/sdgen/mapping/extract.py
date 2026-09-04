from __future__ import annotations

import csv
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from lxml import etree

from sdgen.mapping.model import FieldInfo

XSD_NS = "http://www.w3.org/2001/XMLSchema"
NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T[\d:.]+(Z|[+-]\d{2}:\d{2})?)?$")
MAX_DEPTH = 12


def extract_fields(path: str | Path, kind: str = "auto") -> tuple[str, list[FieldInfo]]:
    path = Path(path)
    kind = kind if kind != "auto" else detect_kind(path)
    if kind == "csv":
        return kind, _from_csv(path)
    if kind == "json":
        return kind, _from_json(json.loads(path.read_text(encoding="utf-8-sig")))
    root = etree.parse(str(path)).getroot()
    if kind == "xsd":
        return kind, _from_xsd(root)
    if kind == "edmx":
        return kind, _from_edmx(root)
    return "xml", _from_xml(root)


def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "json"
    if suffix == ".xsd":
        return "xsd"
    if suffix in (".edmx",):
        return "edmx"
    try:
        root = etree.parse(str(path)).getroot()
    except etree.XMLSyntaxError:
        return "csv" if suffix in (".txt",) else "json"
    local = _local(root.tag)
    if local == "schema" and root.tag.startswith("{" + XSD_NS):
        return "xsd"
    if local == "Edmx":
        return "edmx"
    return "xml"


def _from_csv(path: Path) -> list[FieldInfo]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, dialect=csv.Sniffer().sniff(handle.read(4096)) if handle.seek(0) is None else csv.excel)
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header, data = rows[0], rows[1:]
    fields = []
    for index, name in enumerate(header):
        column = [row[index].strip() for row in data if index < len(row) and row[index].strip()]
        fields.append(FieldInfo(path=name.strip() or f"column_{index + 1}", type=_guess_type(column[0]) if column else "", example=column[0] if column else "", occurs=len(column)))
    return fields


def _from_json(data: Any) -> list[FieldInfo]:
    found: "OrderedDict[str, FieldInfo]" = OrderedDict()
    _walk_json(data, "", found)
    return list(found.values())


def _walk_json(node: Any, prefix: str, found: "OrderedDict[str, FieldInfo]") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _walk_json(value, f"{prefix}/{key}" if prefix else key, found)
    elif isinstance(node, list):
        path = f"{prefix}[]"
        for item in node:
            _walk_json(item, path, found)
    else:
        text = "" if node is None else str(node)
        existing = found.get(prefix)
        if existing is None:
            found[prefix] = FieldInfo(path=prefix, type=_json_type(node), example=text, occurs=1, repeating="[]" in prefix)
        else:
            existing.occurs += 1
            if not existing.example and text:
                existing.example = text


def _from_xml(root: etree._Element) -> list[FieldInfo]:
    found: "OrderedDict[str, FieldInfo]" = OrderedDict()
    _walk_xml(root, _local(root.tag), found)
    return list(found.values())


def _walk_xml(element: etree._Element, path: str, found: "OrderedDict[str, FieldInfo]") -> None:
    for name, value in element.attrib.items():
        _note_xml(found, f"{path}/@{_local(name)}", value)
    children = [c for c in element if isinstance(c.tag, str)]
    if not children:
        _note_xml(found, path, (element.text or "").strip())
        return
    for child in children:
        _walk_xml(child, f"{path}/{_local(child.tag)}", found)


def _note_xml(found: "OrderedDict[str, FieldInfo]", path: str, text: str) -> None:
    existing = found.get(path)
    if existing is None:
        found[path] = FieldInfo(path=path, type=_guess_type(text), example=text, occurs=1)
    else:
        existing.occurs += 1
        existing.repeating = True
        if not existing.example and text:
            existing.example = text


def _from_xsd(root: etree._Element) -> list[FieldInfo]:
    types = {t.get("name"): t for t in root if _local(t.tag) in ("complexType", "simpleType") and t.get("name")}
    fields: list[FieldInfo] = []
    for element in root:
        if _local(element.tag) == "element":
            _walk_xsd_element(element, "", types, fields, 0, required=True)
    return fields


def _walk_xsd_element(element, prefix: str, types: dict, fields: list[FieldInfo], depth: int, required: bool) -> None:
    name = element.get("name") or _strip_prefix(element.get("ref", ""))
    if not name or depth > MAX_DEPTH:
        return
    path = f"{prefix}/{name}" if prefix else name
    min_occurs = element.get("minOccurs", "1")
    max_occurs = element.get("maxOccurs", "1")
    is_required = required and min_occurs != "0"
    repeating = max_occurs == "unbounded" or (max_occurs.isdigit() and int(max_occurs) > 1)
    type_name = _strip_prefix(element.get("type", ""))
    complex_type = None
    for child in element:
        if _local(child.tag) == "complexType":
            complex_type = child
        elif _local(child.tag) == "simpleType":
            type_name = _restriction_base(child) or type_name
    if complex_type is None and type_name in types and _local(types[type_name].tag) == "complexType":
        complex_type = types[type_name]
    if complex_type is None:
        if type_name in types:
            type_name = _restriction_base(types[type_name]) or type_name
        fields.append(FieldInfo(path=path, type=type_name, required=is_required, repeating=repeating, description=_doc(element)))
        return
    for attr in complex_type.iter():
        if _local(attr.tag) == "attribute" and attr.get("name"):
            fields.append(FieldInfo(path=f"{path}/@{attr.get('name')}", type=_strip_prefix(attr.get("type", "")), required=attr.get("use") == "required"))
    for child in complex_type.iter():
        if _local(child.tag) == "element" and child is not element:
            _walk_xsd_element(child, path, types, fields, depth + 1, is_required)


def _from_edmx(root: etree._Element) -> list[FieldInfo]:
    fields: list[FieldInfo] = []
    for entity in root.iter():
        if _local(entity.tag) != "EntityType":
            continue
        entity_name = entity.get("Name", "Entity")
        for prop in entity:
            local = _local(prop.tag)
            if local == "Property":
                fields.append(
                    FieldInfo(
                        path=f"{entity_name}/{prop.get('Name')}",
                        type=prop.get("Type", ""),
                        required=prop.get("Nullable", "true").lower() == "false",
                        description=_edm_label(prop),
                    )
                )
            elif local == "NavigationProperty":
                fields.append(FieldInfo(path=f"{entity_name}/{prop.get('Name')}", type="navigation", required=False, repeating=True))
    return fields


def _edm_label(prop) -> str:
    for attr, value in prop.attrib.items():
        if attr.endswith("}label") or attr.endswith("label"):
            return value
    return ""


def _restriction_base(simple_type) -> str:
    for child in simple_type.iter():
        if _local(child.tag) == "restriction":
            return _strip_prefix(child.get("base", ""))
    return ""


def _doc(element) -> str:
    for child in element.iter():
        if _local(child.tag) == "documentation" and child.text:
            return " ".join(child.text.split())
    return ""


def _strip_prefix(name: str) -> str:
    return name.split(":")[-1] if name else ""


def _local(tag: str) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def _guess_type(text: str) -> str:
    if not text:
        return ""
    if NUMBER_RE.match(text):
        return "decimal" if "." in text else "integer"
    if DATE_RE.match(text):
        return "date"
    return "string"


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "decimal"
    if value is None:
        return ""
    return _guess_type(str(value))
