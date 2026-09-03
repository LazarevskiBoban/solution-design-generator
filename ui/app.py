from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from sdgen.analyze import Analysis, slugify
from sdgen.content import Content, ImageValue, dump_markdown
from sdgen.manifest import FieldSpec, GlobalSpec, Manifest
from sdgen.registry import Registry, safe_name
from sdgen.tools import (
    AnalyzeRequest,
    RenderRequest,
    SkeletonRequest,
    ValidateRequest,
    analyze_template,
    content_skeleton,
    render_document,
    validate_content,
)

ROOT = Path(__file__).resolve().parent.parent
KINDS = ["text", "bullets", "table", "image"]
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def registry() -> Registry:
    return Registry(os.environ.get("SDGEN_TEMPLATES", str(ROOT / "templates")))


def main() -> None:
    st.set_page_config(page_title="sdgen", layout="wide")
    names = registry().names()
    page = st.sidebar.radio("Page", ["Generate", "Templates"], index=0 if names else 1)
    if page == "Templates":
        templates_page()
    else:
        generate_page()


def templates_page() -> None:
    st.header("Templates")
    reg = registry()
    names = reg.names()
    if names:
        rows = [{"template": n, "fields": len(reg.load(n).manifest.fields)} for n in names]
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    else:
        st.caption("No templates yet.")

    st.subheader("Add a template")
    upload = st.file_uploader("PowerPoint deck", type=["pptx"], key="template_upload")
    if upload is None:
        st.info("Upload a deck. It is analysed for fillable sections, which you confirm before saving.")
        return

    token = f"{upload.name}:{upload.size}"
    if st.session_state.get("analysis_token") != token:
        folder = Path(tempfile.mkdtemp(prefix="sdgen-upload-"))
        deck_path = folder / upload.name
        deck_path.write_bytes(upload.getbuffer())
        response = analyze_template(AnalyzeRequest(deck=str(deck_path)))
        st.session_state["analysis_token"] = token
        st.session_state["analysis"] = response.analysis
        st.session_state["deck_path"] = str(deck_path)
        st.session_state["editor_version"] = st.session_state.get("editor_version", 0) + 1

    analysis: Analysis = st.session_state["analysis"]
    version = st.session_state["editor_version"]
    name = st.text_input("Template name", value=safe_name(Path(upload.name).stem), key=f"name:{version}")

    with st.expander("Slides", expanded=False):
        st.dataframe(
            pd.DataFrame([{"slide": s.index, "kind": s.kind, "note": s.reason} for s in analysis.slides]),
            hide_index=True,
        )
    kinds = {s.index: s.kind for s in analysis.slides}
    exclude = st.multiselect(
        "Slides to leave out of every generated document",
        options=[s.index for s in analysis.slides],
        default=[i for i in analysis.exclude if i in kinds],
        format_func=lambda i: f"{i} ({kinds[i]})",
        key=f"exclude:{version}",
    )

    st.subheader("Global replacements")
    st.caption("Text replaced everywhere it appears, such as the integration name in slide titles.")
    globals_df = pd.DataFrame(
        [{"use": True, "key": g.key, "label": g.label, "replaces": g.replaces} for g in analysis.globals],
        columns=["use", "key", "label", "replaces"],
    )
    globals_edit = st.data_editor(
        globals_df,
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        key=f"globals:{version}",
        column_config={
            "use": st.column_config.CheckboxColumn("Use", default=True),
            "key": st.column_config.TextColumn("Key"),
            "label": st.column_config.TextColumn("Label"),
            "replaces": st.column_config.TextColumn("Text to replace"),
        },
    )

    st.subheader("Fields")
    st.caption("Tick the sections to fill, rename keys, and adjust the kind. Unticked rows are ignored.")
    fields_df = pd.DataFrame(
        [
            {
                "use": c.include,
                "slide": c.slide,
                "kind": c.kind,
                "key": c.key,
                "label": c.label,
                "keep_prefix": c.keep_prefix or "",
                "max_chars": c.max_chars,
                "preview": c.preview or c.reason,
                "shape": c.shape_name,
            }
            for c in analysis.candidates
        ],
        columns=["use", "slide", "kind", "key", "label", "keep_prefix", "max_chars", "preview", "shape"],
    )
    fields_edit = st.data_editor(
        fields_df,
        hide_index=True,
        width="stretch",
        height=min(60 + 36 * len(fields_df), 700),
        disabled=["slide", "preview", "shape"],
        key=f"fields:{version}",
        column_config={
            "use": st.column_config.CheckboxColumn("Use"),
            "slide": st.column_config.NumberColumn("Slide"),
            "kind": st.column_config.SelectboxColumn("Kind", options=KINDS, required=True),
            "key": st.column_config.TextColumn("Key"),
            "label": st.column_config.TextColumn("Label"),
            "keep_prefix": st.column_config.TextColumn("Keep prefix"),
            "max_chars": st.column_config.NumberColumn("Max chars", min_value=0, step=10),
            "preview": st.column_config.TextColumn("Preview", width="large"),
            "shape": st.column_config.TextColumn("Shape"),
        },
    )

    if st.button("Save template", type="primary", key="save_template"):
        if not name.strip():
            st.error("Give the template a name.")
            return
        try:
            manifest = _build_manifest(analysis, fields_edit, globals_edit, safe_name(name), list(exclude))
            entry = reg.add(name, st.session_state["deck_path"], manifest)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.success(f"Template '{entry.name}' saved with {len(entry.manifest.fields)} fields. It is now available on the Generate page.")


def _build_manifest(analysis: Analysis, fields_df: pd.DataFrame, globals_df: pd.DataFrame, name: str, exclude: list[int]) -> Manifest:
    candidates = [c.model_copy() for c in analysis.candidates]
    for cand, row in zip(candidates, fields_df.itertuples(index=False)):
        cand.include = bool(row.use)
        cand.kind = row.kind if row.kind in KINDS else cand.kind
        cand.key = slugify(_text(row.key)) if _text(row.key) else cand.key
        cand.label = _text(row.label) or cand.label
        cand.keep_prefix = _text(row.keep_prefix) or None
        cand.max_chars = None if pd.isna(row.max_chars) else int(row.max_chars)
    globals_ = []
    for row in globals_df.itertuples(index=False):
        if not bool(row.use) or not _text(row.replaces) or not _text(row.key):
            continue
        globals_.append(GlobalSpec(key=slugify(_text(row.key)), label=_text(row.label), replaces=_text(row.replaces)))
    edited = Analysis(globals=globals_, candidates=candidates, slides=analysis.slides, exclude=exclude)
    return edited.to_manifest(name)


def generate_page() -> None:
    st.header("Generate")
    reg = registry()
    names = reg.names()
    if not names:
        st.info("No templates yet. Add one on the Templates page.")
        return
    name = st.selectbox("Template", names, key="template_choice")
    entry = reg.load(name)
    manifest = entry.manifest
    prefix = f"{name}:"

    left, right = st.columns(2)
    with left:
        st.download_button(
            "Download empty content file (.md)",
            data=content_skeleton(SkeletonRequest(manifest=manifest)).markdown,
            file_name=f"{name}-content.md",
            help="Fill this in any editor or paste it into a chat, then import it here.",
        )
    with right:
        imported = st.file_uploader("Import a content file (.md)", type=["md", "markdown", "txt"], key=f"{prefix}import")
    if imported is not None:
        token = f"{imported.name}:{imported.size}"
        if st.session_state.get(f"{prefix}import_token") != token:
            response = validate_content(ValidateRequest(manifest=manifest, markdown=imported.getvalue().decode("utf-8")))
            version = st.session_state.get(f"{prefix}version", 0) + 1
            _load_into_state(prefix, version, manifest, response.content)
            st.session_state[f"{prefix}version"] = version
            st.session_state[f"{prefix}import_token"] = token
            st.session_state[f"{prefix}import_warnings"] = response.warnings
            st.rerun()
    for warning in st.session_state.get(f"{prefix}import_warnings", []):
        st.warning(warning)

    version = st.session_state.get(f"{prefix}version", 0)
    globals_: dict[str, str] = {}
    if manifest.globals:
        st.subheader("Document")
        for spec in manifest.globals:
            value = st.text_input(spec.label or spec.key, key=f"{prefix}{version}:g:{spec.key}", help=spec.guidance)
            if value.strip():
                globals_[spec.key] = value.strip()

    images_dir = st.session_state.setdefault("images_dir", tempfile.mkdtemp(prefix="sdgen-images-"))
    fields: dict = {}
    for slide_no, specs in _by_slide(manifest).items():
        st.subheader(f"Slide {slide_no}")
        for spec in specs:
            value = _field_widget(spec, f"{prefix}{version}:f:{spec.key}", images_dir)
            if value is not None:
                fields[spec.key] = value
    content = Content(globals=globals_, fields=fields)

    st.divider()
    col_export, col_blank, col_generate = st.columns([1, 1, 2])
    with col_export:
        st.download_button("Export content (.md)", data=dump_markdown(content, manifest), file_name=f"{name}-content.md")
    with col_blank:
        blank = st.checkbox("Blank unfilled fields", key=f"{prefix}blank")
    with col_generate:
        generate = st.button("Generate document", type="primary", key=f"{prefix}generate")

    if generate:
        out_dir = Path(tempfile.mkdtemp(prefix="sdgen-out-"))
        stem = slugify(globals_.get(next(iter(globals_), ""), "") or name)
        output = out_dir / f"{stem}.pptx"
        response = render_document(
            RenderRequest(template=str(entry.template_path), manifest=manifest, content=content, output=str(output), blank_missing=blank)
        )
        st.session_state[f"{prefix}output"] = (output.name, output.read_bytes(), [str(i) for i in response.issues], response.slides)

    stored = st.session_state.get(f"{prefix}output")
    if stored:
        file_name, data, issues, slides = stored
        for issue in issues:
            (st.error if issue.startswith("error") else st.info if issue.startswith("info") else st.warning)(issue)
        st.success(f"Generated {file_name} with {slides} slides.")
        st.download_button("Download document", data=data, file_name=file_name, mime=PPTX_MIME, key=f"{prefix}download")


def _field_widget(spec: FieldSpec, key: str, images_dir: str):
    if spec.kind in ("text", "bullets"):
        budget = max((b.max_chars or 0) for b in spec.bindings) if spec.bindings else 0
        value = st.text_area(spec.label, key=key, help=spec.guidance, height=170 if spec.kind == "bullets" else 120)
        if budget:
            st.caption(f"{len(value)} of about {budget} characters")
        return value if value.strip() else None
    if spec.kind == "table":
        columns = spec.columns or ["value"]
        initial = st.session_state.get(f"{key}:rows") or []
        frame = pd.DataFrame(initial, columns=columns)
        edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, width="stretch", key=key)
        rows = [{c: _text(v) for c, v in row.items()} for row in edited.to_dict("records")]
        rows = [r for r in rows if any(r.values())]
        return rows or None
    file = st.file_uploader(spec.label, type=["png", "jpg", "jpeg"], key=key, help=spec.guidance)
    if file is not None:
        path = Path(images_dir) / file.name
        path.write_bytes(file.getbuffer())
        return ImageValue(path=str(path))
    imported = st.session_state.get(f"{key}:image")
    return ImageValue(path=imported) if imported else None


def _load_into_state(prefix: str, version: int, manifest: Manifest, content: Content) -> None:
    for spec in manifest.globals:
        st.session_state[f"{prefix}{version}:g:{spec.key}"] = content.globals.get(spec.key, "")
    for spec in manifest.fields:
        key = f"{prefix}{version}:f:{spec.key}"
        value = content.fields.get(spec.key)
        if spec.kind in ("text", "bullets"):
            st.session_state[key] = value if isinstance(value, str) else ""
        elif spec.kind == "table":
            st.session_state[f"{key}:rows"] = value if isinstance(value, list) else []
        elif isinstance(value, ImageValue):
            st.session_state[f"{key}:image"] = value.path


def _by_slide(manifest: Manifest) -> dict[int, list[FieldSpec]]:
    grouped: dict[int, list[FieldSpec]] = {}
    for spec in manifest.fields:
        first = min((b.slide for b in spec.bindings), default=0)
        grouped.setdefault(first, []).append(spec)
    return dict(sorted(grouped.items()))


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


main()
