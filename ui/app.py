from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from sdgen.analyze import Analysis, slugify
from sdgen.blueprint import Blueprint, derive_blueprint
from sdgen.content import Content, ImageValue, dump_markdown
from sdgen.inventory import DeckInfo
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
SECTION_KINDS = ["cover", "static", "divider", "text", "table", "composite", "diagram", "mapping", "references"]
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
        rows = []
        for n in names:
            entry = reg.load(n)
            rows.append({"template": n, "sections": len(entry.blueprint.sections) if entry.blueprint else 0, "fields": len(entry.manifest.fields)})
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    else:
        st.caption("No templates yet.")

    st.subheader("Add a template")
    upload = st.file_uploader("PowerPoint deck", type=["pptx"], key="template_upload")
    if upload is None:
        st.info("Upload a deck once. It is analysed into sections; you confirm what each section needs, then save.")
        return

    token = f"{upload.name}:{upload.size}"
    if st.session_state.get("analysis_token") != token:
        folder = Path(tempfile.mkdtemp(prefix="sdgen-upload-"))
        deck_path = folder / upload.name
        deck_path.write_bytes(upload.getbuffer())
        response = analyze_template(AnalyzeRequest(deck=str(deck_path)))
        st.session_state["analysis_token"] = token
        st.session_state["analysis"] = response.analysis
        st.session_state["deck_info"] = response.deck
        st.session_state["blueprint"] = response.blueprint
        st.session_state["deck_path"] = str(deck_path)
        st.session_state["editor_version"] = st.session_state.get("editor_version", 0) + 1

    analysis: Analysis = st.session_state["analysis"]
    deck_info: DeckInfo = st.session_state["deck_info"]
    blueprint: Blueprint = st.session_state["blueprint"]
    version = st.session_state["editor_version"]
    name = st.text_input("Template name", value=safe_name(Path(upload.name).stem), key=f"name:{version}")

    st.subheader("Sections")
    st.caption("One row per slide. Rename a section, change its kind, edit what it asks for, or untick it to leave it out of every document.")
    outline_df = pd.DataFrame(
        [
            {"use": True, "slide": s.slide, "title": s.title, "kind": s.kind, "ask": s.ask, "optional": s.optional}
            for s in blueprint.sections
        ],
        columns=["use", "slide", "title", "kind", "ask", "optional"],
    )
    outline_edit = st.data_editor(
        outline_df,
        hide_index=True,
        width="stretch",
        height=min(60 + 36 * len(outline_df), 700),
        disabled=["slide"],
        key=f"outline:{version}",
        column_config={
            "use": st.column_config.CheckboxColumn("Use"),
            "slide": st.column_config.NumberColumn("Slide"),
            "title": st.column_config.TextColumn("Section"),
            "kind": st.column_config.SelectboxColumn("Kind", options=SECTION_KINDS, required=True),
            "ask": st.column_config.TextColumn("What to provide", width="large"),
            "optional": st.column_config.CheckboxColumn("Optional"),
        },
    )

    advanced = st.expander("Advanced: fields and replacements", expanded=False)
    with advanced:
        st.caption("The shape-level detail behind the sections. Usually no change is needed.")
    kinds = {s.index: s.kind for s in analysis.slides}
    excluded_by_note = [i for i in analysis.exclude if i in kinds]

    with advanced:
        st.markdown("**Global replacements**")
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

        st.markdown("**Fields**")
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
            unused = [int(r.slide) for r in outline_edit.itertuples(index=False) if not bool(r.use)]
            exclude = sorted(set(unused) | set(excluded_by_note))
            manifest = _build_manifest(analysis, fields_edit, globals_edit, safe_name(name), exclude)
            fresh = derive_blueprint(deck_info, analysis, manifest, safe_name(name))
            final = _apply_outline_edits(fresh, outline_edit)
            entry = reg.add(name, st.session_state["deck_path"], manifest, blueprint=final)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.success(f"Template '{entry.name}' saved with {len(final.sections)} sections. It is now available on the Generate page.")


def _apply_outline_edits(blueprint: Blueprint, outline_df: pd.DataFrame) -> Blueprint:
    edits = {int(r.slide): r for r in outline_df.itertuples(index=False)}
    sections = []
    for section in blueprint.sections:
        row = edits.get(section.slide)
        if row is None:
            sections.append(section)
            continue
        update = {
            "title": _text(row.title) or section.title,
            "kind": row.kind if row.kind in SECTION_KINDS else section.kind,
            "ask": _text(row.ask),
            "optional": bool(row.optional),
        }
        if update["kind"] == "diagram" and section.images == 0:
            update["images"] = 1
        sections.append(section.model_copy(update=update))
    return blueprint.model_copy(update={"sections": sections})


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
        missing = st.selectbox(
            "Unfilled sections",
            ["placeholder", "keep", "blank"],
            format_func={"placeholder": "show a placeholder", "keep": "keep template text", "blank": "leave blank"}.get,
            key=f"{prefix}missing",
        )
    with col_generate:
        generate = st.button("Generate document", type="primary", key=f"{prefix}generate")

    if generate:
        out_dir = Path(tempfile.mkdtemp(prefix="sdgen-out-"))
        stem = slugify(globals_.get(next(iter(globals_), ""), "") or name)
        output = out_dir / f"{stem}.pptx"
        response = render_document(
            RenderRequest(template=str(entry.template_path), manifest=manifest, content=content, output=str(output), missing=missing)
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
