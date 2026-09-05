from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, Field

from sdgen.blueprint import Blueprint
from sdgen.content import Content, ImageValue
from sdgen.manifest import Manifest

PP_SAVE_AS_PDF = 32
PP_ALERTS_NONE = 1
SNIPPET_CHARS = 160
MSO_GROUP = 6
MSO_PLACEHOLDER = 14
FOOTER_PLACEHOLDERS = {13, 15, 16}  # slide number, footer, date
TITLE_PLACEHOLDERS = {1, 3}
FLOW_PREFIX = "Flow "
OVERFLOW_TOLERANCE = 1.1
RETRY_SECONDS = 1.5


class PreviewResult(BaseModel):
    opened: bool
    slides: int = 0
    pdf: str | None = None
    message: str = ""


class TextOverflow(BaseModel):
    """A text box PowerPoint lays out taller than the box itself."""

    slide: int
    shape: str
    ratio: float


class SlideExport(BaseModel):
    files: list[Path] = Field(default_factory=list)
    overflows: list[TextOverflow] = Field(default_factory=list)


def preview(pptx_path: str | Path, pdf_path: str | Path | None = None) -> PreviewResult:
    try:
        with _open_presentation(pptx_path) as presentation:
            slides = presentation.Slides.Count
            pdf = None
            if pdf_path is not None:
                pdf = str(Path(pdf_path).resolve())
                presentation.SaveAs(pdf, PP_SAVE_AS_PDF)
            return PreviewResult(opened=True, slides=slides, pdf=pdf, message="opened in PowerPoint without errors")
    except RuntimeError as exc:
        return PreviewResult(opened=False, message=str(exc))


def export_slides(
    pptx_path: str | Path,
    out_dir: str | Path,
    width: int = 1280,
    attempts: int = 2,
    only: list[int] | None = None,
    check_overflow: bool = True,
    interest: dict[int, set[str]] | None = None,
) -> SlideExport:
    """One PNG per slide (or per slide number in `only`) plus the text boxes PowerPoint lays out taller than they are.

    `interest` names the shapes to check per slide; titles and drawn flow shapes are always checked, and
    without it every text shape is.
    """
    # PowerPoint rejects automation calls while it shows a dialog, so a second attempt often succeeds.
    for attempt in range(1, attempts + 1):
        try:
            return _export_slides(pptx_path, out_dir, width, only, check_overflow, interest)
        except RuntimeError:
            if attempt == attempts:
                raise
            time.sleep(RETRY_SECONDS)
    return SlideExport()


def export_slide_images(pptx_path: str | Path, out_dir: str | Path, width: int = 1280, attempts: int = 2, only: list[int] | None = None) -> list[Path]:
    """Exports one PNG per slide through PowerPoint; raises RuntimeError when that is not possible."""
    return export_slides(pptx_path, out_dir, width, attempts, only, check_overflow=False).files


def overflow_report(pptx_path: str | Path, attempts: int = 2, interest: dict[int, set[str]] | None = None) -> list[TextOverflow]:
    """Text boxes whose laid-out text is taller than the box, as PowerPoint renders the deck."""
    for attempt in range(1, attempts + 1):
        try:
            with _open_presentation(pptx_path) as presentation:
                return _overflows(presentation, None, interest)
        except RuntimeError:
            if attempt == attempts:
                raise
            time.sleep(RETRY_SECONDS)
    return []


@contextmanager
def _open_presentation(pptx_path: str | Path) -> Iterator:
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pywin32 is not installed (pip install -e .[preview])") from exc
    # Server frameworks call this from worker threads, which need their own COM initialisation.
    import pythoncom  # type: ignore

    pythoncom.CoInitialize()
    app = None
    presentation = None
    try:
        app = win32com.client.Dispatch("PowerPoint.Application")
        app.DisplayAlerts = PP_ALERTS_NONE
        presentation = app.Presentations.Open(str(Path(pptx_path).resolve()), ReadOnly=True, Untitled=False, WithWindow=False)
        yield presentation
    except Exception as exc:  # COM errors carry the PowerPoint message text
        raise RuntimeError(str(exc)) from exc
    finally:
        if presentation is not None:
            presentation.Close()
        if app is not None and app.Presentations.Count == 0:
            app.Quit()


def _export_slides(pptx_path: str | Path, out_dir: str | Path, width: int, only: list[int] | None, check_overflow: bool, interest: dict[int, set[str]] | None = None) -> SlideExport:
    target_dir = Path(out_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    with _open_presentation(pptx_path) as presentation:
        height = int(width * presentation.PageSetup.SlideHeight / presentation.PageSetup.SlideWidth)
        count = presentation.Slides.Count
        wanted = [n for n in (only or []) if 1 <= n <= count] if only is not None else list(range(1, count + 1))
        files = []
        for index in wanted:
            target = target_dir / f"slide-{index:02d}.png"
            presentation.Slides.Item(index).Export(str(target), "PNG", width, height)
            files.append(target)
        overflows = _overflows(presentation, wanted, interest) if check_overflow else []
    return SlideExport(files=files, overflows=overflows)


def _overflows(presentation, numbers: list[int] | None, interest: dict[int, set[str]] | None = None) -> list[TextOverflow]:
    found: list[TextOverflow] = []
    wanted = numbers if numbers is not None else range(1, presentation.Slides.Count + 1)
    for index in wanted:
        for shape in _walk(presentation.Slides.Item(index).Shapes):
            if interest is not None and not _of_interest(shape, interest.get(index, set())):
                continue
            ratio = _overflow_ratio(shape)
            if ratio > OVERFLOW_TOLERANCE:
                found.append(TextOverflow(slide=index, shape=str(shape.Name), ratio=round(ratio, 2)))
    return found


def _walk(shapes):
    for shape in shapes:
        if shape.Type == MSO_GROUP:
            yield from _walk(shape.GroupItems)
        else:
            yield shape


def _of_interest(shape, names: set[str]) -> bool:
    try:
        name = str(shape.Name)
        if name in names or name.startswith(FLOW_PREFIX):
            return True
        return shape.Type == MSO_PLACEHOLDER and shape.PlaceholderFormat.Type in TITLE_PLACEHOLDERS
    except Exception:
        return False


def _overflow_ratio(shape) -> float:
    try:
        if shape.HasTable or not shape.HasTextFrame:
            return 0.0
        if shape.Type == MSO_PLACEHOLDER and shape.PlaceholderFormat.Type in FOOTER_PLACEHOLDERS:
            return 0.0
        frame = shape.TextFrame2
        if not frame.HasText:
            return 0.0
        available = shape.Height - frame.MarginTop - frame.MarginBottom
        return frame.TextRange.BoundHeight / available if available > 0 else 0.0
    except Exception:  # shapes without the text properties PowerPoint exposes elsewhere
        return 0.0


def preview_rows(blueprint: Blueprint, manifest: Manifest, content: Content, modes: dict[str, str] | None = None) -> list[dict]:
    """One row per field with a short description of what the slide will show."""
    rows = []
    for section in blueprint.sections:
        mode = (modes or {}).get(section.key, "text")
        for key in section.fields:
            spec = manifest.field(key)
            if spec is None:
                continue
            shown = "(blank)" if mode == "blank" else _describe(content.fields.get(key))
            rows.append({"slide": section.slide, "section": section.title, "field": spec.label, "content": shown})
    return rows


def _describe(value) -> str:
    if value is None or value == "" or value == []:
        return "(empty: placeholder or template text)"
    if isinstance(value, str):
        text = " ".join(value.split())
        return text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS].rstrip() + "…"
    if isinstance(value, ImageValue):
        return "1 image"
    if isinstance(value, list) and value and isinstance(value[0], ImageValue):
        return f"{len(value)} images"
    return f"{len(value)} rows"
