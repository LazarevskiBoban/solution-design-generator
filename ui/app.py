from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from sdgen.analyze import Analysis, slugify
from sdgen.blueprint import Blueprint, derive_blueprint
from sdgen.brief import BRIEF_FIELDS, Brief
from sdgen.content import Content, dump_markdown, load_markdown
from sdgen.design import Design, DesignStore
from sdgen.inventory import DeckInfo
from sdgen.llm import LLMNotConfigured, get_llm
from sdgen.manifest import FieldSpec, GlobalSpec, Manifest
from sdgen.registry import Registry, safe_name
from sdgen.tools import AnalyzeRequest, RenderRequest, analyze_template, render_document
from sdgen.writer import draft_content

ROOT = Path(__file__).resolve().parent.parent
KINDS = ["text", "bullets", "table", "image"]
SECTION_KINDS = ["cover", "static", "divider", "text", "table", "composite", "diagram", "mapping", "references"]
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
NEW_DESIGN = "New design"


def registry() -> Registry:
    return Registry(os.environ.get("SDGEN_TEMPLATES", str(ROOT / "templates")))


def design_store() -> DesignStore:
    return DesignStore(os.environ.get("SDGEN_DESIGNS", str(ROOT / "designs")))


def main() -> None:
    st.set_page_config(page_title="sdgen", layout="wide")
    names = registry().names()
    page = st.sidebar.radio("Page", [NEW_DESIGN, "Templates"], index=0 if names else 1)
    if page == "Templates":
        templates_page()
    else:
        design_page()


# ----------------------------------------------------------------------------- templates


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
        [{"use": True, "slide": s.slide, "title": s.title, "kind": s.kind, "ask": s.ask, "optional": s.optional} for s in blueprint.sections],
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

    kinds = {s.index: s.kind for s in analysis.slides}
    excluded_by_note = [i for i in analysis.exclude if i in kinds]
    with st.expander("Advanced: fields and replacements", expanded=False):
        st.caption("The shape-level detail behind the sections. Usually no change is needed.")
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
        st.success(f"Template '{entry.name}' saved with {len(final.sections)} sections. Start a design on the '{NEW_DESIGN}' page.")


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


# ----------------------------------------------------------------------------- design


def design_page() -> None:
    st.header(NEW_DESIGN)
    reg = registry()
    names = reg.names()
    if not names:
        st.info("No templates yet. Add one on the Templates page.")
        return
    template = st.selectbox("Template", names, key="design_template")
    entry = reg.load(template)
    if entry.blueprint is None:
        st.error("This template has no outline. Re-add it on the Templates page.")
        return
    manifest, blueprint = entry.manifest, entry.blueprint
    store = design_store()
    existing = store.names(template)

    choice = st.selectbox("Design", [NEW_DESIGN] + existing, key=f"design_choice:{template}")
    if choice == NEW_DESIGN:
        raw = st.text_input("Design name", key=f"design_name:{template}", placeholder="for example camt053-bank-statements")
        if not raw.strip():
            st.info("Give the design a name to start.")
            return
        name = safe_name(raw)
    else:
        name = choice

    state_key = f"design:{template}:{name}"
    if state_key not in st.session_state:
        st.session_state[state_key] = store.load(name) if name in existing else Design(name=name, template=template)
        st.session_state[f"{state_key}:v"] = 0
    design: Design = st.session_state[state_key]
    version = st.session_state[f"{state_key}:v"]
    prefix = f"{state_key}:{version}:"

    st.subheader("1. Brief")
    subject = st.text_input("Integration name (used in slide titles)", key=_init(f"{prefix}b:subject", design.brief.subject))
    texts = {}
    for key, label, guidance in BRIEF_FIELDS:
        texts[key] = st.text_area(label, key=_init(f"{prefix}b:{key}", getattr(design.brief, key)), help=guidance, height=110)
    design.brief = Brief(subject=subject.strip(), diagrams=design.brief.diagrams, **texts)

    col_save, col_draft, col_info = st.columns([1, 1, 2])
    with col_save:
        if st.button("Save brief", key=f"{state_key}:save_brief"):
            store.save(design)
            st.success("Brief saved.")
    with col_draft:
        draft_clicked = st.button("Draft sections with AI", type="primary", key=f"{state_key}:draft")
    with col_info:
        st.caption(f"Provider: {os.environ.get('SDGEN_LLM', 'mock')} (set SDGEN_LLM to change)")
    if draft_clicked:
        try:
            llm = get_llm()
        except LLMNotConfigured as exc:
            st.error(str(exc))
        else:
            result = draft_content(design.brief, blueprint, manifest, llm)
            design.content_markdown = result.markdown
            design.llm = result.llm
            store.save(design)
            st.session_state[f"{state_key}:draft_warnings"] = result.warnings
            st.session_state[f"{state_key}:v"] = version + 1
            st.rerun()
    for warning in st.session_state.get(f"{state_key}:draft_warnings", []):
        st.warning(warning)
    if design.llm == "mock" and design.content_markdown:
        st.info("This draft comes from the mock provider and only echoes your brief into each section. Configure a real provider to get written sections.")

    diagram_fields = [
        (section, manifest.field(k))
        for section in blueprint.sections
        if section.kind == "diagram"
        for k in section.fields
        if manifest.field(k) is not None and manifest.field(k).kind == "image"
    ]
    if diagram_fields:
        st.subheader("2. Diagrams")
        for section, spec in diagram_fields:
            files = st.file_uploader(section.title, type=["png", "jpg", "jpeg"], accept_multiple_files=True, key=f"{prefix}img:{spec.key}")
            for file in files or []:
                store.add_image(design, spec.key, file.name, file.getvalue())
            current = design.images.get(spec.key, [])
            if current:
                st.caption("Images: " + ", ".join(current))
                if st.button("Remove images", key=f"{state_key}:clear:{spec.key}"):
                    design.images[spec.key] = []
                    store.save(design)
                    st.rerun()

    st.subheader("3. Review sections")
    imported = st.file_uploader("Import a content file (.md)", type=["md", "markdown", "txt"], key=f"{prefix}import")
    if imported is not None and st.session_state.get(f"{state_key}:import_token") != f"{imported.name}:{imported.size}":
        design.content_markdown = imported.getvalue().decode("utf-8")
        store.save(design)
        st.session_state[f"{state_key}:import_token"] = f"{imported.name}:{imported.size}"
        st.session_state[f"{state_key}:v"] = version + 1
        st.rerun()
    if not design.content_markdown.strip():
        st.info("Draft the sections with AI, import a content file, or fill the sections below by hand.")
    content = load_markdown(design.content_markdown, manifest) if design.content_markdown.strip() else Content()
    edited: dict = {}
    for section in blueprint.sections:
        if section.kind in ("static", "divider"):
            continue
        fields = [manifest.field(k) for k in section.fields]
        fields = [f for f in fields if f is not None and f.kind != "image"]
        if not fields:
            continue
        with st.expander(section.title, expanded=bool(design.content_markdown.strip())):
            if section.ask:
                st.caption(section.ask)
            for spec in fields:
                value = _review_widget(spec, f"{prefix}f:{spec.key}", content.fields.get(spec.key))
                if value is not None:
                    edited[spec.key] = value
    col_save_c, col_export = st.columns(2)
    with col_save_c:
        if st.button("Save sections", key=f"{state_key}:save_content"):
            design.content_markdown = dump_markdown(Content(globals=_globals(subject), fields=edited), manifest)
            store.save(design)
            st.success("Sections saved.")
    with col_export:
        st.download_button("Export content (.md)", data=dump_markdown(Content(globals=_globals(subject), fields=edited), manifest), file_name=f"{name}-content.md", key=f"{state_key}:export")

    st.subheader("4. Generate")
    col_missing, col_generate = st.columns([1, 2])
    with col_missing:
        missing = st.selectbox(
            "Unfilled sections",
            ["placeholder", "keep", "blank"],
            format_func={"placeholder": "show a placeholder", "keep": "keep template text", "blank": "leave blank"}.get,
            key=f"{state_key}:missing",
        )
    with col_generate:
        generate = st.button("Generate document", type="primary", key=f"{state_key}:generate")
    if generate:
        images = {k: v for k, v in store.content(design, manifest).fields.items() if manifest.field(k) and manifest.field(k).kind == "image"}
        final = Content(globals=_globals(subject), fields={**edited, **images})
        design.content_markdown = dump_markdown(Content(globals=_globals(subject), fields=edited), manifest)
        store.save(design)
        out_dir = Path(tempfile.mkdtemp(prefix="sdgen-out-"))
        output = out_dir / f"{slugify(subject) or name}.pptx"
        response = render_document(RenderRequest(template=str(entry.template_path), manifest=manifest, content=final, output=str(output), missing=missing))
        st.session_state[f"{state_key}:output"] = (output.name, output.read_bytes(), [str(i) for i in response.issues], response.slides)

    stored = st.session_state.get(f"{state_key}:output")
    if stored:
        file_name, data, issues, slides = stored
        for issue in issues:
            (st.error if issue.startswith("error") else st.info if issue.startswith("info") else st.warning)(issue)
        st.success(f"Generated {file_name} with {slides} slides.")
        st.download_button("Download document", data=data, file_name=file_name, mime=PPTX_MIME, key=f"{state_key}:download")


def _review_widget(spec: FieldSpec, key: str, value):
    if spec.kind == "table":
        columns = spec.columns or ["value"]
        rows = value if isinstance(value, list) else []
        frame = pd.DataFrame(rows, columns=columns)
        edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, width="stretch", key=key)
        rows = [{c: _text(v) for c, v in row.items()} for row in edited.to_dict("records")]
        rows = [r for r in rows if any(r.values())]
        return rows or None
    budget = max((b.max_chars or 0) for b in spec.bindings) if spec.bindings else 0
    text = st.text_area(spec.label, key=_init(key, value if isinstance(value, str) else ""), help=spec.guidance, height=150 if spec.kind == "bullets" else 120)
    if budget:
        st.caption(f"{len(text)} of about {budget} characters")
    return text if text.strip() else None


def _globals(subject: str) -> dict[str, str]:
    return {"subject": subject.strip()} if subject.strip() else {}


def _init(key: str, value) -> str:
    if key not in st.session_state:
        st.session_state[key] = value
    return key


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


main()
