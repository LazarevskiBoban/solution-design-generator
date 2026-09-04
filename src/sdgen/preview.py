from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel

from sdgen.blueprint import Blueprint
from sdgen.content import Content, ImageValue
from sdgen.manifest import Manifest

PP_SAVE_AS_PDF = 32
PP_ALERTS_NONE = 1
SNIPPET_CHARS = 160


class PreviewResult(BaseModel):
    opened: bool
    slides: int = 0
    pdf: str | None = None
    message: str = ""


def preview(pptx_path: str | Path, pdf_path: str | Path | None = None) -> PreviewResult:
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return PreviewResult(opened=False, message="pywin32 is not installed (pip install -e .[preview])")

    path = Path(pptx_path).resolve()
    app = None
    presentation = None
    try:
        app = win32com.client.Dispatch("PowerPoint.Application")
        app.DisplayAlerts = PP_ALERTS_NONE
        presentation = app.Presentations.Open(str(path), ReadOnly=True, Untitled=False, WithWindow=False)
        slides = presentation.Slides.Count
        pdf = None
        if pdf_path is not None:
            pdf = str(Path(pdf_path).resolve())
            presentation.SaveAs(pdf, PP_SAVE_AS_PDF)
        return PreviewResult(opened=True, slides=slides, pdf=pdf, message="opened in PowerPoint without errors")
    except Exception as exc:  # COM errors carry the PowerPoint message text
        return PreviewResult(opened=False, message=str(exc))
    finally:
        if presentation is not None:
            presentation.Close()
        if app is not None and app.Presentations.Count == 0:
            app.Quit()


def export_slide_images(pptx_path: str | Path, out_dir: str | Path, width: int = 1280, attempts: int = 2) -> list[Path]:
    """Exports one PNG per slide through PowerPoint; raises RuntimeError when that is not possible."""
    # PowerPoint rejects automation calls while it shows a dialog, so a second attempt often succeeds.
    for attempt in range(1, attempts + 1):
        try:
            return _export_slide_images(pptx_path, out_dir, width)
        except RuntimeError:
            if attempt == attempts:
                raise
            time.sleep(1.5)
    return []


def _export_slide_images(pptx_path: str | Path, out_dir: str | Path, width: int) -> list[Path]:
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pywin32 is not installed (pip install -e .[preview])") from exc

    path = Path(pptx_path).resolve()
    target_dir = Path(out_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    app = None
    presentation = None
    try:
        # Server frameworks call this from worker threads, which need their own COM initialisation.
        import pythoncom  # type: ignore

        pythoncom.CoInitialize()
        app = win32com.client.Dispatch("PowerPoint.Application")
        app.DisplayAlerts = PP_ALERTS_NONE
        presentation = app.Presentations.Open(str(path), ReadOnly=True, Untitled=False, WithWindow=False)
        height = int(width * presentation.PageSetup.SlideHeight / presentation.PageSetup.SlideWidth)
        files = []
        for index in range(1, presentation.Slides.Count + 1):
            target = target_dir / f"slide-{index:02d}.png"
            presentation.Slides.Item(index).Export(str(target), "PNG", width, height)
            files.append(target)
        return files
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        if presentation is not None:
            presentation.Close()
        if app is not None and app.Presentations.Count == 0:
            app.Quit()


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
