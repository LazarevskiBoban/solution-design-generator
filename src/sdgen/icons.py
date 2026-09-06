from __future__ import annotations

import base64
import json
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

CATALOGUE_FILE = Path(__file__).with_name("icons.yaml")
DEFAULT_ICON_DIR = Path(__file__).resolve().parents[2] / "assets" / "icons" / "sap"
PNG_DIR_NAME = "png"
PP_SHAPE_FORMAT_PNG = 2
ICON_PX = 256
REPOSITORY = "https://raw.githubusercontent.com/SAP/btp-solution-diagrams/main/assets/shape-libraries-and-editable-presets/"
SERVICE_ICONS_URL = REPOSITORY + "svg/"
GENERIC_LIBRARY_URL = REPOSITORY + "draw.io/20-03-generic-icons/sap-generic-icons-size-M-200302.xml"
GENERIC_PREFIX = "generic-"
GENERIC_TITLE = re.compile(r"(.*?)[ -]?(Highlight|Non-SAP|SAP)[ -]Size [MS]$")


class Icon(BaseModel):
    key: str
    label: str
    file: str = ""


def icon_dir() -> Path:
    return Path(os.environ.get("SDGEN_ICONS") or DEFAULT_ICON_DIR)


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Icon]:
    data = yaml.safe_load(CATALOGUE_FILE.read_text(encoding="utf-8")) or {}
    return {key: Icon(key=key, label=str(item.get("label") or key), file=str(item.get("file") or "")) for key, item in data.items()}


def icon_keys() -> list[str]:
    return list(catalogue())


def fetch_icons(target: Path | None = None) -> list[Path]:
    """Downloads every catalogue file from the SAP BTP Solution Diagrams repository into the icon folder."""
    from urllib.request import urlopen

    folder = target or icon_dir()
    folder.mkdir(parents=True, exist_ok=True)
    wanted = sorted({icon.file for icon in catalogue().values() if icon.file})
    fetched: list[Path] = []
    generic = [name for name in wanted if name.startswith(GENERIC_PREFIX)]
    if generic:
        with urlopen(GENERIC_LIBRARY_URL, timeout=60) as response:
            library = library_svgs(response.read().decode("utf-8"))
        for name in generic:
            if name in library:
                (folder / name).write_bytes(library[name])
                fetched.append(folder / name)
    for name in wanted:
        if name.startswith(GENERIC_PREFIX):
            continue
        with urlopen(SERVICE_ICONS_URL + name, timeout=60) as response:
            (folder / name).write_bytes(response.read())
        fetched.append(folder / name)
    return fetched


def library_svgs(text: str) -> dict[str, bytes]:
    """The SVGs of a draw.io library of the generic SAP icons, keyed by the catalogue file name."""
    match = re.search(r"<mxlibrary>(.*)</mxlibrary>", text, re.S)
    if not match:
        return {}
    found: dict[str, bytes] = {}
    for item in json.loads(match.group(1)):
        title = GENERIC_TITLE.match(str(item.get("title", ""))) if "data" in item else None
        if title is None:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", title.group(1).strip().lower()).strip("-")
        variant = title.group(2).lower().replace("-", "")
        found[f"{GENERIC_PREFIX}{slug}-{variant}.svg"] = base64.b64decode(item["data"].split(",", 1)[1])
    return found


def installed_keys() -> list[str]:
    """Catalogue keys whose PNG rendition exists, the ones drawings can show."""
    return [key for key in icon_keys() if icon_png(key) is not None]


def icon_file(key: str) -> Path | None:
    """The SVG behind a catalogue key, when the file was copied into the icon folder."""
    icon = catalogue().get(key)
    if icon is None or not icon.file:
        return None
    path = icon_dir() / icon.file
    return path if path.is_file() else None


def icon_png(key: str) -> Path | None:
    """The PNG rendition used inside PowerPoint drawings, when it has been built."""
    svg = icon_file(key)
    if svg is None:
        return None
    png = icon_dir() / PNG_DIR_NAME / (svg.stem + ".png")
    return png if png.is_file() else None


def build_pngs(keys: list[str] | None = None) -> list[Path]:
    """Converts the catalogue SVGs to PNG through PowerPoint (python-pptx cannot place SVGs)."""
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pywin32 is not installed (pip install -e .[preview])") from exc
    wanted = [k for k in (keys or icon_keys()) if icon_file(k) is not None]
    if not wanted:
        return []
    target_dir = icon_dir() / PNG_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    pythoncom.CoInitialize()
    app = None
    presentation = None
    built: list[Path] = []
    try:
        app = win32com.client.Dispatch("PowerPoint.Application")
        app.DisplayAlerts = 1
        presentation = app.Presentations.Add(WithWindow=False)
        slide = presentation.Slides.Add(1, 12)  # ppLayoutBlank
        for key in wanted:
            svg = icon_file(key)
            shape = slide.Shapes.AddPicture(str(svg), False, True, 0, 0, 96, 96)
            target = target_dir / (svg.stem + ".png")
            shape.Export(str(target), PP_SHAPE_FORMAT_PNG, ICON_PX, ICON_PX)
            shape.Delete()
            built.append(target)
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        if presentation is not None:
            presentation.Saved = True
            presentation.Close()
        if app is not None and app.Presentations.Count == 0:
            app.Quit()
    return built
