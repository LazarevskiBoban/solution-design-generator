from __future__ import annotations

import logging
import os
import re
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
from sdgen.llm import DEFAULT_AZURE_API_VERSION, DEFAULT_OPENAI_MODEL, LLMError, LLMNotConfigured, get_llm
from sdgen.manifest import FieldSpec, GlobalSpec, Manifest
from sdgen.mapping.extract import extract_fields
from sdgen.mapping.model import MappingEntry, MappingSet, SourceSpec, TargetSpec
from sdgen.mapping.workbook import write_workbook
from sdgen.preview import export_slide_images, preview_rows
from sdgen.registry import Registry, safe_name
from sdgen.tools import AnalyzeRequest, RenderRequest, analyze_template, continuation_slides, render_document
from sdgen.writer import draft_content, writable_sections

ROOT = Path(__file__).resolve().parent.parent
KINDS = ["text", "bullets", "table", "image"]
SECTION_KINDS = ["cover", "static", "divider", "text", "table", "composite", "diagram", "mapping", "references"]
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NEW_DESIGN = "New design"
MAPPINGS = "Mappings"
SAMPLE_TYPES = ["xml", "xsd", "edmx", "json", "csv"]
EMPTY_FIELD_RE = re.compile(r"field '[^']+' \((.+)\) is empty$")
SECTION_MODES = {"text": "Use the text below", "keep": "Keep the template text", "blank": "Leave the slide blank"}


def registry() -> Registry:
    return Registry(os.environ.get("SDGEN_TEMPLATES", str(ROOT / "templates")))


def design_store() -> DesignStore:
    return DesignStore(os.environ.get("SDGEN_DESIGNS", str(ROOT / "designs")))


def provider_settings() -> tuple[str, dict[str, str]]:
    options = ["mock", "azure", "openai"]
    default = (_secret("SDGEN_LLM") or "mock").strip().lower()
    with st.sidebar.expander("AI provider", expanded=False):
        provider = st.selectbox("Provider", options, index=options.index(default) if default in options else 0, key="llm_provider")
        settings: dict[str, str] = {}
        if provider == "azure":
            settings["endpoint"] = st.text_input(
                "Azure OpenAI endpoint",
                key=_init("llm_endpoint", _secret("AZURE_OPENAI_ENDPOINT")),
                help="Foundry > Keys and endpoints > Azure OpenAI endpoint, like https://<resource>.openai.azure.com/ (not the project endpoint).",
            )
            settings["model"] = st.text_input("Deployment name", key=_init("llm_deployment", _secret("AZURE_OPENAI_DEPLOYMENT")), help="The name of the model deployment in Foundry.")
            settings["api_key"] = st.text_input("API key", type="password", key=_init("llm_azure_key", _secret("AZURE_OPENAI_API_KEY")))
            settings["api_version"] = st.text_input("API version", key=_init("llm_api_version", _secret("AZURE_OPENAI_API_VERSION") or DEFAULT_AZURE_API_VERSION))
        elif provider == "openai":
            settings["model"] = st.text_input("Model", key=_init("llm_model", _secret("SDGEN_OPENAI_MODEL") or DEFAULT_OPENAI_MODEL))
            settings["api_key"] = st.text_input("API key", type="password", key=_init("llm_openai_key", _secret("OPENAI_API_KEY")))
        if provider != "mock":
            st.caption("Values typed here last for this browser session. Put them in .streamlit/secrets.toml or environment variables to keep them.")
    return provider, {k: v.strip() for k, v in settings.items()}


def _secret(name: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, "") or "")
    except Exception:
        return ""


def main() -> None:
    st.set_page_config(page_title="sdgen", layout="wide")
    names = registry().names()
    page = st.sidebar.radio("Page", [NEW_DESIGN, MAPPINGS, "Templates"], index=0 if names else 2)
    st.session_state["llm"] = provider_settings()
    if page == "Templates":
        templates_page()
    elif page == MAPPINGS:
        mappings_page()
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
        with st.expander("Remove a template", expanded=False):
            target = st.selectbox("Template", names, key="template_remove_choice")
            used_by = design_store().names(target)
            question = f"Remove template '{target}' and its stored deck?"
            if used_by:
                question += f" {len(used_by)} design(s) built on it stay on disk but disappear from the New design page: {', '.join(used_by)}."
            _confirm_delete("template_remove", "Remove template", question, lambda: _remove_template(reg, target))
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

    col_choice, col_delete = st.columns([4, 1], vertical_alignment="bottom")
    with col_choice:
        choice = st.selectbox("Design", [NEW_DESIGN] + existing, key=f"design_choice:{template}")
    if choice == NEW_DESIGN:
        raw = st.text_input("Design name", key=f"design_name:{template}", placeholder="for example camt053-bank-statements")
        if not raw.strip():
            st.info("Give the design a name to start.")
            return
        name = safe_name(raw)
    else:
        name = choice
        with col_delete:
            _confirm_delete(
                f"design:{template}:{name}:delete",
                "Delete design",
                f"Delete design '{name}' with its brief, sections, images and mappings? This cannot be undone.",
                lambda: _delete_design(store, template, name),
            )

    state_key = f"design:{template}:{name}"
    if state_key not in st.session_state:
        st.session_state[state_key] = store.load(name) if name in existing else Design(name=name, template=template)
        st.session_state[f"{state_key}:v"] = 0
    design: Design = st.session_state[state_key]
    version = st.session_state[f"{state_key}:v"]
    prefix = f"{state_key}:{version}:"
    snapshot = design.model_dump_json()
    provider, settings = st.session_state.get("llm", ("mock", {}))
    model = settings.get("model", "")

    with st.expander("1. Brief", expanded=design.brief.is_empty):
        subject = st.text_input("Integration name (used in slide titles)", key=_init(f"{prefix}b:subject", design.brief.subject))
        texts = {}
        for key, label, guidance in BRIEF_FIELDS:
            texts[key] = st.text_area(label, key=_init(f"{prefix}b:{key}", getattr(design.brief, key)), help=guidance, height=110)
        design.brief = Brief(subject=subject.strip(), diagrams=design.brief.diagrams, **texts)

        col_draft, col_info = st.columns([1, 3], vertical_alignment="center")
        with col_draft:
            draft_clicked = st.button("Draft sections with AI", type="primary", key=f"{state_key}:draft")
        with col_info:
            st.caption(f"Provider: {provider}" + (f" ({model})" if model else "") + ". Change it under AI provider in the sidebar. Changes are saved automatically.")
        if draft_clicked:
            try:
                llm = get_llm(provider, **settings)
                if design.mapping is not None and not design.brief.mapping_summary.strip():
                    design.brief.mapping_summary = _mapping_note(design)
                with st.spinner(f"Drafting sections with {provider}. This can take a minute."):
                    result = draft_content(design.brief, blueprint, manifest, llm)
            except (LLMNotConfigured, LLMError) as exc:
                st.error(str(exc))
            else:
                if not result.content.fields:
                    st.error("The reply did not use the expected section headings, so nothing was filled. Try again or choose another model.")
                else:
                    design.content_markdown = result.markdown
                    design.llm = result.llm
                    store.save(design)
                    total = sum(len(s["fields"]) for s in writable_sections(blueprint, manifest))
                    shown = f"{result.llm} ({model})" if model else result.llm
                    st.session_state[f"{state_key}:draft_done"] = f"Drafted {len(result.content.fields)} of {total} fields with {shown}. Review them in step 3, then generate."
                    st.session_state[f"{state_key}:draft_warnings"] = result.warnings
                    st.session_state[f"{state_key}:v"] = version + 1
                    st.rerun()
        if st.session_state.get(f"{state_key}:draft_done"):
            st.success(st.session_state[f"{state_key}:draft_done"])
        _show_draft_warnings(st.session_state.get(f"{state_key}:draft_warnings", []))
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
        uploaded = sum(len(design.images.get(spec.key, [])) for _, spec in diagram_fields)
        with st.expander(f"2. Diagrams: {uploaded} image(s) uploaded for {len(diagram_fields)} slots", expanded=False):
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
    with st.expander("Import a content file (.md)", expanded=False):
        imported = st.file_uploader("Content file", type=["md", "markdown", "txt"], key=f"{prefix}import", label_visibility="collapsed")
        if imported is not None and st.session_state.get(f"{state_key}:import_token") != f"{imported.name}:{imported.size}":
            design.content_markdown = imported.getvalue().decode("utf-8")
            store.save(design)
            st.session_state[f"{state_key}:import_token"] = f"{imported.name}:{imported.size}"
            st.session_state[f"{state_key}:v"] = version + 1
            st.rerun()
    content = load_markdown(design.content_markdown, manifest) if design.content_markdown.strip() else Content()
    sections = []
    for section in blueprint.sections:
        if section.kind in ("static", "divider"):
            continue
        fields = [f for f in (manifest.field(k) for k in section.fields) if f is not None and f.kind != "image"]
        if fields:
            sections.append((section, fields))
    if not design.content_markdown.strip():
        st.info("Nothing drafted yet. Draft the sections in step 1, import a content file, or write them here. Generate also drafts on its own when nothing has been drafted.")
    if sections:
        labels = {s.key: f"{s.title}  ({_section_status(s, fields, design, content)})" for s, fields in sections}
        picked = st.selectbox("Section", [s.key for s, _ in sections], format_func=labels.get, key=f"{state_key}:section")
        section, fields = next((s, f) for s, f in sections if s.key == picked)
        with st.container(border=True):
            if section.ask:
                st.caption(section.ask)
            mode = st.radio(
                "Slide content",
                list(SECTION_MODES),
                format_func=SECTION_MODES.get,
                horizontal=True,
                key=_init(f"{prefix}mode:{section.key}", design.modes.get(section.key, "text")),
            )
            if mode == "text":
                design.modes.pop(section.key, None)
                for spec in fields:
                    value = _review_widget(spec, f"{prefix}f:{spec.key}", content.fields.get(spec.key))
                    if value in (None, "", []):
                        content.fields.pop(spec.key, None)
                    else:
                        content.fields[spec.key] = value
            else:
                design.modes[section.key] = mode
                if mode == "keep":
                    _show_original(entry, fields)
                else:
                    st.caption("The fields of this slide stay empty in the document.")
        design.content_markdown = dump_markdown(Content(globals=_globals(subject), fields=content.fields), manifest)
        st.download_button("Export content (.md)", data=design.content_markdown, file_name=f"{name}-content.md", key=f"{state_key}:export")
    else:
        st.caption("This template has no sections to write.")

    st.subheader("4. Generate")
    col_missing, col_preview, col_generate = st.columns([1, 1, 1], vertical_alignment="bottom")
    with col_missing:
        missing = st.selectbox(
            "Unfilled sections",
            ["placeholder", "keep", "blank"],
            format_func={"placeholder": "show a placeholder", "keep": "keep template text", "blank": "leave blank"}.get,
            key=f"{state_key}:missing",
        )
    with col_preview:
        preview_clicked = st.button("Preview slides", key=f"{state_key}:preview", help="Shows the slides as pictures with the current sections. Needs PowerPoint on this machine; otherwise a table per slide is shown.")
    with col_generate:
        generate = st.button("Generate document", type="primary", key=f"{state_key}:generate")
    if preview_clicked:
        output, response = _render_design(entry, store, design, subject, name, missing)
        notes = [str(i) for i in response.issues if not str(i).startswith("info")]
        try:
            with st.spinner("Rendering slide pictures with PowerPoint."):
                pictures = [p.read_bytes() for p in export_slide_images(output, output.parent / "png")]
            _preview_dialog(pictures, notes)
        except RuntimeError as exc:
            logging.getLogger("sdgen.ui").warning("slide preview fell back to the table: %s", exc)
            fields, _ = _render_fields(design, entry)
            rows = preview_rows(blueprint, manifest, Content(fields=fields), design.modes)
            _preview_table_dialog(rows, [f"Slide pictures are not available: {exc}. This is what each slide will contain."] + notes)
    if generate:
        drafted_now = False
        if not design.llm and provider != "mock" and not design.brief.is_empty:
            try:
                llm = get_llm(provider, **settings)
                with st.spinner(f"Drafting the sections with {provider} before generating. This can take a minute."):
                    result = draft_content(design.brief, blueprint, manifest, llm)
            except (LLMNotConfigured, LLMError) as exc:
                st.error(str(exc))
                st.stop()
            merged = {**result.content.fields, **content.fields}
            design.content_markdown = dump_markdown(Content(globals=_globals(subject), fields=merged), manifest)
            design.llm = result.llm
            drafted_now = True
            st.session_state[f"{state_key}:draft_done"] = f"Drafted {len(result.content.fields)} fields with {result.llm} while generating. Text you typed yourself was kept."
            st.session_state[f"{state_key}:draft_warnings"] = result.warnings
        elif not any(v not in ("", [], None) for v in _render_fields(design, entry)[0].values()):
            st.warning("No section text yet, so the document will only show placeholders. Draft the sections with AI first, or fill them under Review sections.")
        store.save(design)
        output, response = _render_design(entry, store, design, subject, name, missing)
        st.session_state[f"{state_key}:output"] = (output.name, output.read_bytes(), [str(i) for i in response.issues], response.slides)
        if drafted_now:
            st.session_state[f"{state_key}:v"] = version + 1
            st.rerun()

    stored = st.session_state.get(f"{state_key}:output")
    if stored:
        file_name, data, issues, slides = stored
        problems = [i for i in issues if not i.startswith("info")]
        st.success(f"Generated {file_name} with {slides} slides." + (f" {len(problems)} remark(s), see below." if problems else ""))
        col_deck, col_book = st.columns(2)
        with col_deck:
            st.download_button("Download document", data=data, file_name=file_name, mime=PPTX_MIME, key=f"{state_key}:download")
        if design.mapping is not None and design.mapping.sources:
            with col_book:
                workbook = write_workbook(design.mapping, store.workbook_path(design))
                st.download_button("Download mapping workbook", data=workbook.read_bytes(), file_name=design.workbook_name, mime=XLSX_MIME, key=f"{state_key}:download_xlsx")
        if problems:
            with st.expander(f"{len(problems)} remark(s) from the generator", expanded=False):
                for issue in problems:
                    (st.error if issue.startswith("error") else st.warning)(issue)

    if design.model_dump_json() != snapshot:
        store.save(design)


def _section_status(section, fields, design: Design, content: Content) -> str:
    mode = design.modes.get(section.key, "text")
    if mode != "text":
        return SECTION_MODES[mode]
    filled = sum(1 for f in fields if content.fields.get(f.key) not in (None, "", []))
    return f"{filled} of {len(fields)} filled"


def _show_original(entry, fields) -> None:
    original = entry.original.fields if entry.original else {}
    if not any(original.get(f.key) not in (None, "", []) for f in fields):
        st.caption("The template holds no text for this slide, so the unfilled-section setting from step 4 applies.")
        return
    st.caption("The slide keeps the text of the template:")
    for spec in fields:
        value = original.get(spec.key)
        if isinstance(value, list) and value:
            st.dataframe(pd.DataFrame(value), hide_index=True, width="stretch")
        elif isinstance(value, str) and value:
            st.text_area(spec.label, value=value, disabled=True, height=100, key=f"original:{entry.name}:{spec.key}")


def _delete_design(store: DesignStore, template: str, name: str) -> None:
    store.delete(name)
    for key in [k for k in st.session_state if str(k) == f"design:{template}:{name}" or str(k).startswith(f"design:{template}:{name}:")]:
        del st.session_state[key]
    st.session_state.pop(f"design_choice:{template}", None)


def _remove_template(reg: Registry, target: str) -> None:
    reg.remove(target)
    for key in ("template_remove_choice", "design_template"):
        st.session_state.pop(key, None)


def _render_fields(design: Design, entry) -> tuple[dict, dict[str, str]]:
    manifest = entry.manifest
    content = load_markdown(design.content_markdown, manifest) if design.content_markdown.strip() else Content()
    fields = {k: v for k, v in content.fields.items() if manifest.field(k) is None or manifest.field(k).kind != "image"}
    field_modes: dict[str, str] = {}
    for section in entry.blueprint.sections:
        mode = design.modes.get(section.key, "text")
        if mode == "text":
            continue
        for key in section.fields:
            if mode == "keep":
                original = entry.original.fields.get(key) if entry.original else None
                if original in (None, "", []):
                    fields.pop(key, None)
                else:
                    fields[key] = original
            else:
                fields.pop(key, None)
                field_modes[key] = "blank"
    return fields, field_modes


@st.dialog("Slide preview", width="large")
def _preview_dialog(pictures: list[bytes], notes: list[str]) -> None:
    for note in notes:
        st.warning(note)
    columns = st.columns(2)
    for index, png in enumerate(pictures):
        with columns[index % 2]:
            st.image(png, caption=f"Slide {index + 1}", width="stretch")


@st.dialog("Slide preview", width="large")
def _preview_table_dialog(rows: list[dict], notes: list[str]) -> None:
    if notes:
        st.error(notes[0])
    for note in notes[1:]:
        st.warning(note)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


# ----------------------------------------------------------------------------- mappings


def mappings_page() -> None:
    st.header(MAPPINGS)
    reg = registry()
    names = reg.names()
    if not names:
        st.info("No templates yet. Add one on the Templates page.")
        return
    template = st.selectbox("Template", names, key="mapping_template")
    store = design_store()
    existing = store.names(template)
    if not existing:
        st.info(f"Create a design on the '{NEW_DESIGN}' page first; mappings belong to a design.")
        return
    name = st.selectbox("Design", existing, key=f"mapping_design:{template}")
    state_key = f"design:{template}:{name}"
    if state_key not in st.session_state:
        st.session_state[state_key] = store.load(name)
        st.session_state[f"{state_key}:v"] = 0
    design: Design = st.session_state[state_key]

    st.subheader("1. Target")
    st.caption("The unified payload the integration sends, for example the S/4HANA API metadata (EDMX), an XSD, or a sample payload.")
    target_file = st.file_uploader("Target definition", type=SAMPLE_TYPES, key=f"{state_key}:target_upload")
    if target_file is not None and st.session_state.get(f"{state_key}:target_token") != f"{target_file.name}:{target_file.size}":
        path = store.add_mapping_file(design, target_file.name, target_file.getvalue())
        kind, fields = extract_fields(path)
        target = TargetSpec(name=Path(target_file.name).stem, file=target_file.name, kind=kind, fields=fields)
        if design.mapping is None:
            design.mapping = MappingSet(name=design.name, target=target)
        else:
            design.mapping.target = target
        store.save(design)
        st.session_state[f"{state_key}:target_token"] = f"{target_file.name}:{target_file.size}"
        st.rerun()
    mapping = design.mapping
    if mapping is None:
        st.info("Upload the target definition to start.")
        return
    st.caption(f"Target: {mapping.target.name} ({mapping.target.kind}, {len(mapping.target.fields)} fields)")

    st.subheader("2. Sources")
    st.caption("One sample per sending party, for example one CAMT.053 file per bank. Each source gets its own sheet in the workbook.")
    col_name, col_file, col_add = st.columns([1, 2, 1])
    with col_name:
        source_name = st.text_input("Source name", key=f"{state_key}:source_name", placeholder="Bank A")
    with col_file:
        source_file = st.file_uploader("Source sample (XML, JSON, CSV)", type=SAMPLE_TYPES, key=f"{state_key}:source_upload")
    with col_add:
        st.write("")
        st.write("")
        add_source = st.button("Add source", key=f"{state_key}:add_source", disabled=source_file is None or not source_name.strip())
    if add_source and source_file is not None:
        path = store.add_mapping_file(design, source_file.name, source_file.getvalue())
        kind, fields = extract_fields(path)
        clean = source_name.strip()
        mapping.sources = [s for s in mapping.sources if s.name != clean] + [SourceSpec(name=clean, file=source_file.name, kind=kind, fields=fields)]
        store.save(design)
        st.rerun()
    if not mapping.sources:
        st.info("Add at least one source sample.")
        return

    st.subheader("3. Map fields")
    tabs = st.tabs([s.name for s in mapping.sources])
    for tab, source in zip(tabs, mapping.sources):
        with tab:
            st.caption(f"{source.file} ({source.kind}, {len(source.fields)} fields). Pick the source field for each target field; describe constants or conversions as a rule.")
            entries = mapping.entries_for(source.name)
            rows = []
            for field in mapping.target.fields:
                entry = entries.get(field.path)
                rows.append(
                    {
                        "target": field.path,
                        "type": field.type,
                        "required": "yes" if field.required else "",
                        "source_field": entry.source_path if entry else "",
                        "rule": entry.rule if entry else "",
                        "example": entry.example if entry else "",
                        "note": entry.note if entry else "",
                    }
                )
            frame = pd.DataFrame(rows, columns=["target", "type", "required", "source_field", "rule", "example", "note"])
            edited = st.data_editor(
                frame,
                hide_index=True,
                width="stretch",
                height=min(60 + 36 * len(rows), 600),
                disabled=["target", "type", "required"],
                key=f"{state_key}:grid:{source.name}",
                column_config={
                    "target": st.column_config.TextColumn("Target field", width="large"),
                    "type": st.column_config.TextColumn("Type", width="small"),
                    "required": st.column_config.TextColumn("Required", width="small"),
                    "source_field": st.column_config.SelectboxColumn("Source field", options=[""] + [f.path for f in source.fields], width="large"),
                    "rule": st.column_config.TextColumn("Transformation rule"),
                    "example": st.column_config.TextColumn("Example"),
                    "note": st.column_config.TextColumn("Notes"),
                },
            )
            for row in edited.itertuples(index=False):
                mapping.set_entry(
                    MappingEntry(
                        target_path=row.target,
                        source=source.name,
                        source_path=_text(row.source_field),
                        rule=_text(row.rule),
                        example=_text(row.example),
                        note=_text(row.note),
                    )
                )
            if st.button(f"Remove source '{source.name}'", key=f"{state_key}:remove:{source.name}"):
                mapping.sources = [s for s in mapping.sources if s.name != source.name]
                mapping.entries = [e for e in mapping.entries if e.source != source.name]
                store.save(design)
                st.rerun()

    st.subheader("4. Workbook")
    st.text(mapping.summary_text())
    col_save, col_brief, col_download = st.columns(3)
    with col_save:
        if st.button("Save mappings", key=f"{state_key}:save_mapping"):
            store.save(design)
            st.success("Mappings saved.")
    with col_brief:
        if st.button("Use summary in brief", key=f"{state_key}:summary_to_brief"):
            design.brief.mapping_summary = _mapping_note(design)
            store.save(design)
            st.session_state[f"{state_key}:v"] = st.session_state.get(f"{state_key}:v", 0) + 1
            st.success("Mapping summary written into the brief.")
    with col_download:
        workbook = write_workbook(mapping, store.workbook_path(design))
        st.download_button("Download workbook (.xlsx)", data=workbook.read_bytes(), file_name=design.workbook_name, mime=XLSX_MIME, key=f"{state_key}:download_workbook")


def _mapping_note(design: Design) -> str:
    return f"{design.mapping.summary_text()}\nDetailed field mapping: {design.workbook_name}"


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


def _show_draft_warnings(warnings: list[str]) -> None:
    empty = []
    for warning in warnings:
        match = EMPTY_FIELD_RE.match(warning)
        if match:
            empty.append(match.group(1))
        else:
            st.warning(warning)
    if empty:
        st.warning(f"{len(empty)} fields came back empty: " + ", ".join(empty))


def _render_design(entry, store: DesignStore, design: Design, subject: str, name: str, missing: str):
    manifest, blueprint = entry.manifest, entry.blueprint
    fields, field_modes = _render_fields(design, entry)
    images = {k: v for k, v in store.content(design, manifest).fields.items() if manifest.field(k) and manifest.field(k).kind == "image"}
    final = Content(globals=_globals(subject), fields={**fields, **images})
    out_dir = Path(tempfile.mkdtemp(prefix="sdgen-out-"))
    output = out_dir / f"{slugify(subject) or name}.pptx"
    response = render_document(
        RenderRequest(
            template=str(entry.template_path),
            manifest=manifest,
            content=final,
            output=str(output),
            missing=missing,
            continue_on=continuation_slides(blueprint),
            field_modes=field_modes,
        )
    )
    return output, response


def _confirm_delete(key: str, label: str, question: str, on_confirm) -> None:
    if st.button(label, key=key):
        _confirm_dialog(key, question, on_confirm)


@st.dialog("Please confirm")
def _confirm_dialog(key: str, question: str, on_confirm) -> None:
    st.warning(question)
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("Yes, delete", type="primary", key=f"{key}:yes"):
            on_confirm()
            st.rerun()
    with col_no:
        if st.button("Cancel", key=f"{key}:no"):
            st.rerun()


def _init(key: str, value) -> str:
    if key not in st.session_state:
        st.session_state[key] = value
    return key


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


if __name__ == "__main__":
    main()
