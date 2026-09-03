from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

PP_SAVE_AS_PDF = 32
PP_ALERTS_NONE = 1


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
