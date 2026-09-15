from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from sdgen.analyze import Analysis, slugify
from sdgen.blueprint import Blueprint, composite_fields, derive_blueprint
from sdgen.brief import BRIEF_FIELDS, DEVELOPER_FIELDS, FACT_CELLS, FACT_PREFIX, FACTS_BY_KEY, SEPARATED_FIELDS, Brief, FactQuestion, brief_lint, fact_questions, split_joined
from sdgen.content import Content, detail_key, dump_markdown, is_detail_key, load_markdown, stem_of
from sdgen.design import Design, DesignStore
from pptx import Presentation

from sdgen.inventory import DeckInfo, find_shape
from sdgen.llm import DEFAULT_AZURE_API_VERSION, DEFAULT_OPENAI_MODEL, LLMError, LLMNotConfigured, default_deployment, get_llm
from sdgen.manifest import FieldSpec, GlobalSpec, Manifest
from sdgen.mapping.extract import extract_fields
from sdgen.mapping.model import MappingEntry, MappingSet, SourceSpec, TargetSpec
from sdgen.mapping.workbook import write_workbook
from sdgen import material as materials
from sdgen.drawio import to_drawio
from sdgen.flow import lane_mismatches, plan_flows, system_lanes, to_mermaid, uses_sap, walkthrough_text
from sdgen.icons import icon_keys, installed_keys
from sdgen.plan import OPEN_QUESTIONS_KEY, SOURCES, WALKTHROUGH_SUFFIX, FlowRequest, SectionDecision, SectionPlan, active_extras, apply_plan, default_plan, detail_prototypes, extended_blueprint, extended_manifest, extra_slides, leftover_texts, open_question_rows, open_questions_extras, open_questions_text, plan_sections, walkthrough_extras
from sdgen.preview import export_slides
from sdgen.references import lookup as lookup_reference
from sdgen.registry import Registry, safe_name
from sdgen.tools import AnalyzeRequest, RenderRequest, analyze_template, continuation_slides, render_document
from sdgen.writer import draft_content, extract_facts, redraft_section, resplit_lines, writable_sections

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
VIEWER_CSS = "<style>div[data-testid='stDialog'] div[data-testid='stImage'] img{width:auto !important;max-width:100%;max-height:calc(100vh - 300px);display:block;margin:0 auto}</style>"
# Full view: the dialog box fills the window, its title bar and everything under the picture are hidden, the picture takes the rest.
FULL_VIEW_CSS = (
    "<style>"
    "div[data-testid='stDialog']{padding:0 !important}"
    "div[data-testid='stDialog'] > div{width:100vw !important;max-width:100vw !important;height:100vh !important;max-height:100vh !important;margin:0 !important;border-radius:0 !important}"
    "div[data-testid='stDialog'] [role='dialog'] > h2{display:none}"
    "div[data-testid='stDialog'] div[data-testid='stImage'] img{width:100% !important;height:calc(100vh - 110px) !important;max-height:none !important;object-fit:contain;display:block;margin:0 auto}"
    ".st-key-viewer_details{display:none}"
    "</style>"
)
SHORTCUTS = {"previous": "Left", "next": "Right", "up": "Up", "down": "Down", "hide": "Delete"}
PREVIEW_WIDTH = 1600  # pixels per exported slide picture, enough for the full view on a wide screen
STEPS = ["brief", "plan", "write", "diagrams", "review", "generate"]
DONE_ICON = "✅"
LAYOUT_NAMES = {"bands": "System bands, left to right", "columns": "Lane columns, top to bottom", "sequence": "Sequence, numbered arrows top to bottom"}
DIAGRAM_FORMATS = {
    "shapes": "Shapes on the slide only",
    "drawio": "Shapes on the slide plus a draw.io file to open, adjust and export",
    "mermaid": "Shapes on the slide plus a Mermaid file",
}


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
            raw = st.text_input(
                "Deployments",
                key=_init("llm_deployment", _secret("AZURE_OPENAI_DEPLOYMENT")),
                help="Deployment names from Foundry, comma separated. gpt-5 is used by default when it is listed, otherwise the first one. For example gpt-4.1, gpt-5, gpt-4o-mini.",
            )
            deployments = [d.strip() for d in raw.split(",") if d.strip()]
            settings["model"] = default_deployment(deployments)
            settings["deployments"] = ",".join(deployments)
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
    store = design_store()
    names = reg.names()
    if names:
        rows = []
        for n in names:
            entry = reg.load(n)
            rows.append({"template": n, "sections": len(entry.blueprint.sections) if entry.blueprint else 0, "fields": len(entry.manifest.fields)})
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        with st.expander("Remove a template", expanded=False):
            target = st.selectbox("Template", names, key="template_remove_choice")
            used_by = store.names(target)
            question = f"Remove template '{target}' and its stored deck?"
            if used_by:
                question += f" The {len(used_by)} design(s) built on it go with it, drawings included: {', '.join(used_by)}."
            _confirm_delete("template_remove", "Remove template", question, lambda: _remove_template(reg, store, target))
        with st.expander("Re-analyze a template", expanded=False):
            st.caption("Runs the analysis again on the stored original deck and keeps your section titles, kinds and field keys. Field detection and text budgets follow the current analyzer.")
            again = st.selectbox("Template", names, key="template_reanalyze_choice")
            if st.button("Re-analyze", key="template_reanalyze"):
                try:
                    entry = reg.reanalyze(again)
                except FileNotFoundError as exc:
                    st.error(str(exc))
                else:
                    st.success(f"Template '{entry.name}' analysed again: {len(entry.blueprint.sections)} sections, {len(entry.manifest.fields)} fields.")
    else:
        st.caption("No templates yet.")
    owners = store.templates()
    if owners:
        with st.expander("Remove a design", expanded=False):
            st.caption("Deletes the design with its brief, sections, images, drawings and mappings. The template stays.")
            victim = st.selectbox("Design", list(owners), format_func=lambda n: f"{n} ({owners[n]})" + ("" if owners[n] in names else ", template removed"), key="design_remove_choice")
            _confirm_delete("design_remove", "Remove design", f"Delete design '{victim}' with its brief, sections, images, drawings and mappings? This cannot be undone.", lambda: _delete_design(store, owners[victim], victim))

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
                "Delete this design",
                f"Delete design '{name}' with its brief, sections, images and mappings? This cannot be undone.",
                lambda: _delete_design(store, template, name),
            )

    state_key = f"design:{template}:{name}"
    if state_key not in st.session_state:
        st.session_state[state_key] = store.load(name) if name in existing else Design(name=name, template=template)
        st.session_state[f"{state_key}:v"] = 0
    design: Design = st.session_state[state_key]
    extras = active_extras(design.plan) + walkthrough_extras(design, entry.blueprint, entry.manifest, store.flows(design), _section_order(design, entry.blueprint)) + open_questions_extras(design, entry.blueprint, entry.manifest)
    walked = {e.key[: -len(WALKTHROUGH_SUFFIX)] for e in extras if e.generated and e.key.endswith(WALKTHROUGH_SUFFIX)}
    if extras:
        entry = entry.model_copy(update={"manifest": extended_manifest(entry.manifest, entry.blueprint, extras), "blueprint": extended_blueprint(entry.blueprint, extras)})
        manifest, blueprint = entry.manifest, entry.blueprint
    version = st.session_state[f"{state_key}:v"]
    prefix = f"{state_key}:{version}:"
    snapshot = design.model_dump_json()
    provider, settings = st.session_state.get("llm", ("mock", {}))
    settings = dict(settings)
    deployments = [d for d in settings.get("deployments", "").split(",") if d]
    model = settings.get("model", "")
    if len(deployments) > 1:
        chosen_key = f"{state_key}:deployment"
        if st.session_state.get(chosen_key) not in deployments:
            remembered = design.llm.split(":", 1)[1] if design.llm.startswith(f"{provider}:") else ""
            st.session_state[chosen_key] = remembered if remembered in deployments else default_deployment(deployments)
        settings["model"] = st.selectbox("Model for this design", deployments, key=chosen_key, help="Used for the section plan, the facts, the writing and the diagrams. gpt-5 is picked by default when it is listed.")
        model = settings["model"]

    written = bool(design.content_markdown.strip())
    diagram_fields = [
        (section, manifest.field(k))
        for section in blueprint.sections
        if section.kind == "diagram"
        for k in section.fields
        if manifest.field(k) is not None and manifest.field(k).kind == "image"
    ]
    present = [s for s in STEPS if s != "diagrams" or diagram_fields]
    step = _current_step(state_key, design, present)
    with st.expander("1. Brief", expanded=step == "brief", icon=_done("brief" in design.completed), key=_expander_key(state_key, "brief", step)):
        subject = st.text_input("Integration name (used in slide titles)", key=_init(f"{prefix}b:subject", design.brief.subject))
        texts = {}
        developer_shown = False
        for key, label, guidance in BRIEF_FIELDS:
            if key in DEVELOPER_FIELDS and not developer_shown:
                st.markdown("**For the developers**")
                st.caption("What they need to build and test it. Empty boxes are fine; the draft marks gaps.")
                developer_shown = True
            texts[key] = st.text_area(label, key=_init(f"{prefix}b:{key}", getattr(design.brief, key)), help=guidance, height=110)
        facts: dict[str, str] = {}
        questions = fact_questions(blueprint, manifest)
        if questions:
            st.markdown("**Facts this template needs**")
            st.caption("Short answers, used only in the slides named on each field. Leave unknown ones empty and the draft writes [TBC] there instead of guessing.")
            columns = st.columns(2)
            for index, question in enumerate(questions):
                spec = question.spec
                help_text = spec.guidance + (" Used by: " + ", ".join(question.used_by) + "." if question.used_by else "")
                with columns[index % 2]:
                    widget_key = _init(f"{prefix}fact:{spec.key}", design.brief.facts.get(spec.key, ""))
                    if spec.multiline:
                        facts[spec.key] = st.text_area(spec.label, key=widget_key, help=help_text, height=90)
                    else:
                        facts[spec.key] = st.text_input(spec.label, key=widget_key, help=help_text)
        design.brief = Brief(
            subject=subject.strip(),
            material=design.brief.material,
            facts={k: v.strip() for k, v in facts.items() if v.strip()},
            **texts,
        )
        for key, message in brief_lint(design.brief):
            text_col, button_col = st.columns([5, 1])
            text_col.warning(message)
            if (key in SEPARATED_FIELDS or key.startswith(FACT_PREFIX)) and button_col.button("Split into lines", key=f"{state_key}:split:{key}", help="The model breaks the pasted line into one entry per line without changing a word."):
                _split_field(state_key, design, store, key, provider, settings, version)
        if st.session_state.get(f"{state_key}:brief_note"):
            st.info(st.session_state.pop(f"{state_key}:brief_note"))
        if questions:
            if st.button("Pre-fill facts from the notes", key=f"{state_key}:prefill", help="The model copies facts it finds word for word in the text boxes above into the empty fact fields."):
                try:
                    llm = get_llm(provider, **settings)
                    with st.spinner("Reading the notes."):
                        found = extract_facts(design.brief, questions, llm)
                except (LLMNotConfigured, LLMError) as exc:
                    st.error(str(exc))
                else:
                    added = {k: v for k, v in found.items() if not design.brief.facts.get(k, "").strip()}
                    design.brief.facts.update(added)
                    store.save(design)
                    labels = ", ".join(FACTS_BY_KEY[k].label for k in added if k in FACTS_BY_KEY)
                    st.session_state[f"{state_key}:facts_note"] = f"Filled {len(added)} fact(s) from the notes: {labels}." if added else "No new facts found in the notes."
                    st.session_state[f"{state_key}:v"] = version + 1
                    st.rerun()
            if st.session_state.get(f"{state_key}:facts_note"):
                st.info(st.session_state[f"{state_key}:facts_note"])
        st.markdown("**Reference material**")
        st.caption("Pictures of diagrams, notes and links the model reads next to the brief. A picture is transcribed once into text you can correct; the picture itself also goes to the model when it draws a diagram. Tag an item with the slides it is about, or leave it for all of them.")
        _material_editor(state_key, prefix, version, design, store, blueprint, provider, settings, {s.key: spec.key for s, spec in diagram_fields})
        _complete_section(state_key, design, store, "brief", present)

    with st.expander(_plan_title(design), expanded=step == "plan", icon=_done("plan" in design.completed), key=_expander_key(state_key, "plan", step)):
        st.caption("The model decides which slides apply to this design, proposes titles for slides named after another project, extra slides for the developer content and the diagrams to draw. Confirm to apply it: hidden slides, kept slides and titles follow the plan.")
        if st.button("Plan sections with AI", key=f"{state_key}:plan"):
            try:
                llm = get_llm(provider, **settings)
                planner = llm.with_effort("medium") if hasattr(llm, "with_effort") else llm
                with st.spinner("Planning the sections."):
                    leftovers = leftover_texts(entry.template_path, entry.manifest, entry.blueprint)
                    proposed = plan_sections(design.brief, blueprint, manifest, planner, images={k for k, v in design.images.items() if v}, leftovers=leftovers)
            except (LLMNotConfigured, LLMError) as exc:
                st.error(str(exc))
            else:
                st.session_state[f"{state_key}:proposed_plan"] = proposed
                st.session_state[f"{state_key}:v"] = version + 1
                st.rerun()
        proposed = st.session_state.get(f"{state_key}:proposed_plan")
        plan = proposed or design.plan
        if plan is not None:
            if proposed is not None:
                st.info(f"Proposal from {proposed.model or provider}. Adjust the grid, then confirm.")
            edited_plan = _plan_editor(plan, blueprint, f"{prefix}plan", pictures={m.id: m.label for m in design.brief.material if m.kind == "image" and m.file})
            col_confirm, col_discard = st.columns([1, 4])
            with col_confirm:
                if st.button("Confirm plan", type="primary", key=f"{state_key}:plan_confirm"):
                    apply_plan(edited_plan, design, blueprint)
                    store.apply_reference_pictures(design, {s.key: spec.key for s, spec in diagram_fields})
                    store.save(design)
                    st.session_state.pop(f"{state_key}:proposed_plan", None)
                    st.session_state[f"{state_key}:v"] = version + 1
                    st.rerun()
            with col_discard:
                if proposed is not None and st.button("Discard proposal", key=f"{state_key}:plan_discard"):
                    st.session_state.pop(f"{state_key}:proposed_plan", None)
                    st.rerun()
        _complete_section(state_key, design, store, "plan", present)

    with st.expander(_write_title(design), expanded=step == "write", icon=_done("write" in design.completed), key=_expander_key(state_key, "write", step)):
        st.caption("This sends the brief and the facts to the model and fills every slide section (about a minute). Run it after the section plan. Nothing appears under Review sections until it has run; Generate runs it on its own when nothing has been written yet.")
        col_draft, col_info = st.columns([1, 3], vertical_alignment="center")
        with col_draft:
            draft_clicked = st.button("Write the slides from the brief", type="primary", key=f"{state_key}:draft", help="Fills every section from the brief and the facts. Sections you edited after the previous run are kept.")
        with col_info:
            st.caption(f"Provider: {provider}" + (f" ({model})" if model else "") + ". Change the model above the brief or the provider under AI provider in the sidebar. Changes are saved automatically.")
        if draft_clicked:
            _run_draft(state_key, entry, design, store, subject, version, provider, settings)
        if st.session_state.get(f"{state_key}:draft_done"):
            st.success(st.session_state[f"{state_key}:draft_done"])
        _show_draft_warnings(st.session_state.get(f"{state_key}:draft_warnings", []))
        if design.llm == "mock" and design.content_markdown:
            st.info("This draft comes from the mock provider and only echoes your brief into each section. Configure a real provider to get written sections.")
        _complete_section(state_key, design, store, "write", present)

    if diagram_fields and st.session_state.pop(f"{state_key}:draw_after_write", False):
        waiting = _pending_flow_sections(design, entry, store)
        if waiting:
            _diagram_format_dialog(state_key, waiting, {f.section: f for f in design.plan.flows}, provider, settings)
    if diagram_fields:
        flows = store.flows(design)
        uploaded = sum(len(design.images.get(spec.key, [])) for _, spec in diagram_fields)
        drawn = sum(1 for section, _ in diagram_fields if section.key in flows)
        requests = {f.section: f for f in (design.plan.flows if design.plan else [])}
        pending = [section for section, spec in diagram_fields if section.key not in flows and not design.images.get(spec.key) and section.key not in design.hidden]
        with st.expander(f"4. Diagrams: {uploaded} image(s) uploaded, {drawn} drawn from the brief, {len(diagram_fields)} slots", expanded=step == "diagrams", icon=_done("diagrams" in design.completed), key=_expander_key(state_key, "diagrams", step)):
            st.caption("Draw asks the model for the flow (systems, steps, arrows), draws it as editable shapes on the slide and keeps a draw.io and a Mermaid file next to it; a popup lets you pick the format to work with. An uploaded image always wins over a drawing. A drawing on a slide without a text box of its own gets a 'how it works' slide after it with the numbered steps.")
            _show_icon_note(design)
            lanes = system_lanes(design.brief)
            if lanes:
                st.caption("Lanes of every diagram, from 'Systems in the flow' in the brief: " + "; ".join(f"{lane.title} ({lane.role})" for lane in lanes) + ".")
            else:
                st.warning("Fill 'Systems in the flow' in the brief first, one per line: system | source, middleware or target | keep, change or new. The systems become the lanes of every diagram; without them the lanes only follow the data direction.")
                if st.button("Extract systems from the brief", key=f"{state_key}:extract_systems", help="The model lists the systems it finds in the brief text; check them in the Brief step afterwards."):
                    _extract_systems(state_key, design, store, provider, settings, version)
            if pending and st.button(f"Draw {len(pending)} diagram(s) from the brief" if lanes else f"Draw {len(pending)} diagram(s) anyway", key=f"{state_key}:draw_all"):
                _diagram_format_dialog(state_key, pending, requests, provider, settings)
            if st.session_state.get(f"{state_key}:flow_note"):
                st.info(st.session_state.pop(f"{state_key}:flow_note"))
            for section, spec in diagram_fields:
                st.markdown(f"**{design.titles.get(section.key, section.title)}**" + (" (hidden)" if section.key in design.hidden else ""))
                files = st.file_uploader("Image", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key=f"{prefix}img:{spec.key}", label_visibility="collapsed")
                for file in files or []:
                    store.add_image(design, spec.key, file.name, file.getvalue())
                current = design.images.get(spec.key, [])
                reference = store.reference_picture(design, spec.key)
                if reference is not None:
                    st.caption(f"Picture from the reference material: {reference.label}. It takes the slot instead of a drawing, and no how-it-works slide follows it.")
                    if st.button("Use the drawing instead", key=f"{state_key}:unpic:{section.key}"):
                        _set_reference_picture(design, store, blueprint, section.key, "", {s.key: sp.key for s, sp in diagram_fields})
                        st.rerun()
                elif current:
                    st.caption("Images: " + ", ".join(current))
                    if st.button("Remove images", key=f"{state_key}:clear:{spec.key}"):
                        design.images[spec.key] = []
                        store.save(design)
                        st.rerun()
                flow = flows.get(section.key)
                col_draw, col_layout, col_drawio, col_mermaid, col_remove = st.columns(5)
                if flow is not None:
                    layouts = list(LAYOUT_NAMES)
                    other = layouts[(layouts.index(flow.layout) + 1) % len(layouts)] if flow.layout in layouts else layouts[0]
                    with col_layout:
                        if st.button(f"Switch to {LAYOUT_NAMES[other].split(',')[0].lower()}", key=f"{state_key}:layout:{section.key}", help="Redraws the same flow in the next arrangement (bands, columns, sequence); no model call."):
                            store.save_flow(design, section.key, flow.model_copy(update={"layout": other}))
                            st.rerun()
                    chosen = design.diagram_formats.get(section.key, design.diagram_format)
                    reference = lookup_reference(flow.reference)
                    st.caption("Drawn from the brief: " + " → ".join(n.label for n in flow.nodes[:6]) + (" …" if len(flow.nodes) > 6 else "") + f". Format: {DIAGRAM_FORMATS.get(chosen, chosen)}." + (" Open the file, adjust it, export a PNG and upload it above to replace the drawing." if chosen != "shapes" else "") + (" A how-it-works slide follows it." if section.key in walked else " The slide's own text explains it.") + (f" Reference: [{reference[1].title}]({reference[0].url(reference[1])})." if reference else ""))
                    with col_draw:
                        if st.button("Redraw from brief", key=f"{state_key}:draw:{section.key}"):
                            _diagram_format_dialog(state_key, [section], requests, provider, settings)
                    with col_drawio:
                        st.download_button("draw.io file", data=to_drawio(flow), file_name=f"{section.key}.drawio", key=f"{state_key}:drawio:{section.key}")
                    with col_mermaid:
                        st.download_button("Mermaid", data=to_mermaid(flow), file_name=f"{section.key}.mmd", key=f"{state_key}:mmd:{section.key}")
                    with col_remove:
                        if st.button("Remove drawing", key=f"{state_key}:undraw:{section.key}"):
                            store.delete_flow(design, section.key)
                            st.rerun()
                else:
                    with col_draw:
                        if st.button("Draw from brief", key=f"{state_key}:draw:{section.key}"):
                            _diagram_format_dialog(state_key, [section], requests, provider, settings)
            _complete_section(state_key, design, store, "diagrams", present)

    content = load_markdown(design.content_markdown, manifest) if design.content_markdown.strip() else Content()
    sections = _writable(blueprint, manifest)
    with st.expander(_review_title(sections, content), expanded=step == "review", icon=_done("review" in design.completed), key=_expander_key(state_key, "review", step)):
        with st.popover("Import a content file (.md)"):
            imported = st.file_uploader("Content file", type=["md", "markdown", "txt"], key=f"{prefix}import", label_visibility="collapsed")
            if imported is not None and st.session_state.get(f"{state_key}:import_token") != f"{imported.name}:{imported.size}":
                design.content_markdown = imported.getvalue().decode("utf-8")
                store.save(design)
                st.session_state[f"{state_key}:import_token"] = f"{imported.name}:{imported.size}"
                st.session_state[f"{state_key}:v"] = version + 1
                st.rerun()
        if not written:
            st.warning("Nothing written yet. The sections below stay empty until the model writes them from your brief.")
            if st.button("Write the slides from the brief now", type="primary", key=f"{state_key}:draft2"):
                _run_draft(state_key, entry, design, store, subject, version, provider, settings)
        if sections:
            labels = {s.key: f"{s.title}  ({_section_status(s, fields, design, content)})" for s, fields in sections}
            picked = st.selectbox("Section", [s.key for s, _ in sections], format_func=labels.get, key=f"{state_key}:section")
            section, fields = next((s, f) for s, f in sections if s.key == picked)
            fields_before = dict(content.fields)
            with st.container(border=True):
                _section_editor(entry, design, content, section, fields, prefix)
            design.content_markdown = dump_markdown(Content(globals=_globals(subject), fields=content.fields), manifest)
            if content.fields != fields_before and st.session_state.get(f"{state_key}:board"):
                st.session_state[f"{state_key}:stale"] = set(st.session_state.get(f"{state_key}:stale", set())) | {picked}
            st.download_button("Export content (.md)", data=design.content_markdown, file_name=f"{name}-content.md", key=f"{state_key}:export")
        else:
            st.caption("This template has no sections to write.")
        _complete_section(state_key, design, store, "review", present)

    with st.expander("6. Generate", expanded=step == "generate", icon=_done("generate" in design.completed), key=_expander_key(state_key, "generate", step)):
        col_missing, col_preview, col_generate = st.columns([1, 1, 1], vertical_alignment="bottom")
        with col_missing:
            missing = st.selectbox(
                "Unfilled sections",
                ["placeholder", "keep", "blank"],
                format_func={"placeholder": "show a placeholder", "keep": "keep template text", "blank": "leave blank"}.get,
                key=f"{state_key}:missing",
            )
        with col_preview:
            preview_clicked = st.button("Preview slides", key=f"{state_key}:preview", help="Builds the slide board below: one picture per slide with edit, hide and move buttons. Pictures need PowerPoint on this machine; without it the board shows the slide texts.")
        with col_generate:
            generate = st.button("Generate document", type="primary", key=f"{state_key}:generate")
        if preview_clicked:
            output, response, keyed = _render_design(entry, store, design, subject, name, missing)
            notes = [str(i) for i in response.issues if not str(i).startswith("info") and not i.message.startswith("check ")]
            overflows: dict[int, list[str]] = {}
            pictures: dict[int, bytes] = {}
            try:
                with st.spinner("Rendering slide pictures with PowerPoint."):
                    exported = export_slides(output, output.parent / "png", width=PREVIEW_WIDTH, interest=_overflow_interest(entry, response, keyed))
                pictures = {position: p.read_bytes() for position, p in enumerate(exported.files, 1)}
                for item in exported.overflows:
                    overflows.setdefault(item.slide, []).append(item.shape)
            except RuntimeError as exc:
                logging.getLogger("sdgen.ui").warning("slide preview without pictures: %s", exc)
                notes.insert(0, f"Slide pictures are not available: {exc}. The board shows the slide texts instead.")
            current, _ = _render_fields(design, entry)
            entries = _board_entries(entry, design, response, current, pictures, overflows)
            st.session_state[f"{state_key}:board"] = (entries, _with_overflow_note(entries, notes))
            st.session_state.pop(f"{state_key}:stale", None)
            st.session_state[f"{state_key}:viewer"] = 0
            _slide_viewer(state_key, entry)
        if generate:
            drafted_now = False
            if not design.llm and provider != "mock" and not design.brief.is_empty:
                try:
                    llm = get_llm(provider, **settings)
                    with st.spinner(f"Drafting the sections with {provider} before generating. This can take a minute."):
                        result = draft_content(design.brief, blueprint, manifest, llm, original=entry.original, skip_sections=_skipped_sections(design), token_only=_kept_sections(design))
                except (LLMNotConfigured, LLMError) as exc:
                    st.error(str(exc))
                    st.stop()
                merged = {**result.content.fields, **content.fields}
                design.content_markdown = dump_markdown(Content(globals=_globals(subject), fields=merged), manifest)
                design.last_draft = result.markdown
                design.llm = result.llm
                drafted_now = True
                st.session_state[f"{state_key}:draft_done"] = f"Drafted {len(result.content.fields)} fields with {result.llm} while generating. Text you typed yourself was kept."
                st.session_state[f"{state_key}:draft_warnings"] = result.warnings
            elif not any(v not in ("", [], None) for v in _render_fields(design, entry)[0].values()):
                st.warning("No section text yet, so the document will only show placeholders. Draft the sections with AI first, or fill them under Review sections.")
            store.save(design)
            output, response, _ = _render_design(entry, store, design, subject, name, missing)
            st.session_state[f"{state_key}:output"] = (output.name, output.read_bytes(), [str(i) for i in response.issues], response.slides)
            if drafted_now:
                st.session_state[f"{state_key}:v"] = version + 1
                st.rerun()

        board = st.session_state.get(f"{state_key}:board")
        if board:
            _slide_board(state_key, entry, design, store, board[0], board[1])

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
                with st.popover(f"{len(problems)} remark(s) from the generator"):
                    for issue in problems:
                        (st.error if issue.startswith("error") else st.warning)(issue)
        _complete_section(state_key, design, store, "generate", present)

    if design.model_dump_json() != snapshot:
        store.save(design)


def _writable(blueprint: Blueprint, manifest: Manifest) -> list[tuple]:
    result = []
    for section in blueprint.sections:
        if section.kind in ("static", "divider") or section.generated:
            continue
        fields = [f for f in (manifest.field(k) for k in section.fields) if f is not None and f.kind != "image"]
        if fields:
            result.append((section, fields))
    return result


def _section_status(section, fields, design: Design, content: Content) -> str:
    if section.key in design.hidden:
        return "hidden"
    mode = design.modes.get(section.key, "text")
    if mode != "text":
        return SECTION_MODES[mode]
    filled = sum(1 for f in fields if content.fields.get(f.key) not in (None, "", []))
    return f"{filled} of {len(fields)} filled"


def _section_editor(entry, design: Design, content: Content, section, fields, prefix: str) -> None:
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
        detailed = set(composite_fields(entry.blueprint, entry.manifest))
        for spec in fields:
            value = _review_widget(spec, f"{prefix}f:{spec.key}", content.fields.get(spec.key))
            if value in (None, "", []):
                content.fields.pop(spec.key, None)
            else:
                content.fields[spec.key] = value
            if spec.key in detailed:
                more_spec = spec.model_copy(update={"label": f"{spec.label} (detail slide, optional)", "bindings": []})
                more = _review_widget(more_spec, f"{prefix}f:{detail_key(spec.key)}", content.fields.get(detail_key(spec.key)))
                if more in (None, "", []):
                    content.fields.pop(detail_key(spec.key), None)
                else:
                    content.fields[detail_key(spec.key)] = more
    else:
        design.modes[section.key] = mode
        if mode == "keep":
            _show_original(entry, fields, prefix)
        else:
            st.caption("The fields of this slide stay empty in the document.")


def _show_original(entry, fields, prefix: str) -> None:
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
            st.text_area(spec.label, value=value, disabled=True, height=100, key=f"{prefix}orig:{spec.key}")


def _slide_board(state_key: str, entry, design: Design, store: DesignStore, entries: list[dict], notes: list[str]) -> None:
    sections = {s.key: s for s in entry.blueprint.sections}
    shown = _visible_entries(entries, design, entry.blueprint)
    col_info, col_open = st.columns([3, 1], vertical_alignment="center")
    with col_info:
        st.markdown(f"**Slide board:** {len(shown)} slides" + (f", {len(design.hidden)} hidden" if design.hidden else "") + ". The viewer shows one slide at a time with edit, move and hide actions.")
    with col_open:
        if st.button("Open slide viewer", key=f"{state_key}:vw:open"):
            _slide_viewer(state_key, entry)
    for index, note in enumerate(notes):
        (st.error if index == 0 and note.startswith("Slide pictures") else st.warning)(note)
    if design.hidden:
        with st.popover(f"Hidden slides ({len(design.hidden)})"):
            for key in list(design.hidden):
                col_name, col_button = st.columns([4, 1], vertical_alignment="center")
                col_name.write(design.titles.get(key, sections[key].title) if key in sections else key)
                if col_button.button("Unhide", key=f"{state_key}:bd:unhide:{key}"):
                    design.hidden.remove(key)
                    store.save(design)
                    st.rerun()


@st.dialog("Slide viewer", width="large", on_dismiss="rerun")
def _slide_viewer(state_key: str, entry) -> None:
    st.html(VIEWER_CSS)
    design: Design = st.session_state[state_key]
    store = design_store()
    blueprint, manifest = entry.blueprint, entry.manifest
    entries, notes = st.session_state[f"{state_key}:board"]
    sections = {s.key: s for s in blueprint.sections}
    shown = _visible_entries(entries, design, blueprint)
    if not shown:
        st.info("Every slide is hidden. Unhide slides on the page.")
        return
    if notes and notes[0].startswith("Slide pictures"):
        st.error(notes[0])
    index_key = f"{state_key}:viewer"
    index = min(max(st.session_state.get(index_key, 0), 0), len(shown) - 1)
    # Keys stay off while a section is being edited, so arrows and Delete keep their meaning in the text boxes.
    keys_on = not st.session_state.get(f"{state_key}:vw:edit:{shown[index]['section']}", False)
    key_for = (lambda action: SHORTCUTS[action]) if keys_on else (lambda action: None)

    col_prev, col_pick, col_next, col_full = st.columns([1, 4, 1, 1], vertical_alignment="bottom")
    with col_full:
        if st.toggle("Full view", key=f"{state_key}:vw:full", help="Fit the slide to the screen, like a zoomed picture; switch it off for the section actions"):
            st.html(FULL_VIEW_CSS)
    with col_prev:
        if st.button("Previous", key=f"{state_key}:vw:prev", disabled=index == 0, shortcut=key_for("previous"), help="Left arrow"):
            index -= 1
    with col_next:
        if st.button("Next", key=f"{state_key}:vw:next", disabled=index >= len(shown) - 1, shortcut=key_for("next"), help="Right arrow"):
            index += 1
    labels = [f"{i + 1}. {e['title']}" for i, e in enumerate(shown)]
    with col_pick:
        picked = st.selectbox("Slide", range(len(shown)), format_func=lambda i: labels[i], index=index, label_visibility="collapsed")
    if picked != index and not st.session_state.get(f"{state_key}:vw:jumped"):
        index = picked
    st.session_state[index_key] = index
    st.session_state[f"{state_key}:vw:jumped"] = False

    item = shown[index]
    section = sections.get(item["section"])
    stale = set(st.session_state.get(f"{state_key}:stale", set()))
    if item["png"]:
        st.image(item["png"], width="stretch")
    else:
        with st.container(border=True):
            st.markdown(f"### {item['title']}")
            st.write(item["text"] or "No text on this slide yet.")
            st.caption("No picture for this slide yet. Press Refresh slide.")
    with st.container(key="viewer_details"):
        st.caption(f"Slide {index + 1} of {len(shown)}: {item['title']}" + (". Edited since the picture was taken, press Refresh slide." if item["section"] in stale else ""))
        if item.get("overflow"):
            st.error("PowerPoint lays out more text than fits the box: " + ", ".join(item["overflow"]) + ". Shorten the text or split it.")
        if item.get("checks"):
            st.warning("Check: " + "; ".join(item["checks"]) + ".")
        if section is None:
            return
        order = _section_order(design, blueprint)
        position = order.index(section.key)
        fields = [f for f in (manifest.field(k) for k in section.fields) if f is not None and f.kind != "image"]
        col_edit, col_refresh, col_up, col_down, col_hide = st.columns(5)
        with col_edit:
            editing = st.toggle("Edit section", key=f"{state_key}:vw:edit:{section.key}", disabled=not fields)
        with col_refresh:
            if st.button("Refresh slide", key=f"{state_key}:vw:refresh:{section.key}", help="Render this slide again; saving a section does this on its own"):
                _refresh_slide(state_key, entry, design, store, section.key)
        with col_up:
            if st.button("Move up", key=f"{state_key}:vw:up:{section.key}", disabled=position == 0, shortcut=key_for("up"), help="Up arrow"):
                _move_section(design, blueprint, section.key, -1)
                store.save(design)
                _jump_to_section(state_key, entries, design, blueprint, section.key)
                st.rerun(scope="fragment")
        with col_down:
            if st.button("Move down", key=f"{state_key}:vw:down:{section.key}", disabled=position >= len(order) - 1, shortcut=key_for("down"), help="Down arrow"):
                _move_section(design, blueprint, section.key, 1)
                store.save(design)
                _jump_to_section(state_key, entries, design, blueprint, section.key)
                st.rerun(scope="fragment")
        with col_hide:
            if st.button("Hide slide", key=f"{state_key}:vw:hide:{section.key}", disabled=section.kind == "cover", shortcut=key_for("hide"), help="Delete key. Drops this slide from the document; unhide it on the slide board"):
                design.hidden.append(section.key)
                store.save(design)
                st.toast(f"Hidden: {item['title']}. Unhide it on the slide board.")
                st.rerun(scope="fragment")

        if editing and fields:
            version = st.session_state[f"{state_key}:v"]
            content = load_markdown(design.content_markdown, manifest) if design.content_markdown.strip() else Content()
            modes_before = dict(design.modes)
            with st.container(border=True):
                _section_editor(entry, design, content, section, fields, f"{state_key}:{version}:view:")
                if design.modes != modes_before:
                    store.save(design)
                    _refresh_slide(state_key, entry, design, store, section.key)
                if st.button("Save section", type="primary", key=f"{state_key}:vw:save:{section.key}"):
                    design.content_markdown = dump_markdown(Content(globals=_globals(design.brief.subject), fields=content.fields), manifest)
                    store.save(design)
                    st.session_state[f"{state_key}:v"] = version + 1
                    _refresh_slide(state_key, entry, design, store, section.key)
                instruction = st.text_input("Redraft with an instruction", key=f"{state_key}:vw:instruction:{section.key}", placeholder="for example: shorter, name the three banks, add the retry rule")
                if st.button("Redraft this section", key=f"{state_key}:vw:redraft:{section.key}", disabled=not instruction.strip()):
                    try:
                        llm = _current_llm(state_key)
                        with st.spinner("Rewriting the section."):
                            fields_new = redraft_section(design.brief, blueprint, manifest, section.key, instruction, content, llm)
                    except (LLMNotConfigured, LLMError) as exc:
                        st.error(str(exc))
                    else:
                        if not fields_new:
                            st.error("The reply did not use the expected headings; nothing changed.")
                        else:
                            content.fields.update(fields_new)
                            design.content_markdown = dump_markdown(Content(globals=_globals(design.brief.subject), fields=content.fields), manifest)
                            store.save(design)
                            st.session_state[f"{state_key}:v"] = version + 1
                            _refresh_slide(state_key, entry, design, store, section.key)

def _refresh_slide(state_key: str, entry, design: Design, store: DesignStore, key: str) -> None:
    with st.spinner("Updating the slide."):
        _refresh_section_pictures(state_key, entry, design, store, key)
    st.rerun(scope="fragment")


def _jump_to_section(state_key: str, entries: list[dict], design: Design, blueprint: Blueprint, key: str) -> None:
    shown = _visible_entries(entries, design, blueprint)
    st.session_state[f"{state_key}:viewer"] = next((i for i, e in enumerate(shown) if e["section"] == key), 0)
    st.session_state[f"{state_key}:vw:jumped"] = True


def _visible_entries(entries: list[dict], design: Design, blueprint: Blueprint) -> list[dict]:
    sections = {s.key: s for s in blueprint.sections}
    shown: list[dict] = []
    for key in _section_order(design, blueprint):
        if key in design.hidden:
            continue
        matches = [e for e in entries if e["section"] == key]
        shown.extend(_continued(matches) or [{"section": key, "title": design.titles.get(key, sections[key].title), "png": None, "text": ""}])
    shown.extend(e for e in entries if e["section"] not in sections)
    return shown


def _continued(matches: list[dict]) -> list[dict]:
    shown: list[dict] = []
    copies = 0
    for item in matches:
        if item.get("detail"):
            shown.append(item)
            continue
        shown.append(item if copies == 0 else {**item, "title": f"{item['title']} (cont.)"})
        copies += 1
    return shown


def _board_entries(entry, design: Design, response, current: dict, pictures: dict[int, bytes], overflows: dict[int, list[str]]) -> list[dict]:
    """One board entry per output slide: its section, title, picture, text and overflowing boxes; detail slides carry their field."""
    blueprint = entry.blueprint
    by_slide = {s.slide: s for s in blueprint.sections if not s.key.startswith("extra_")}
    by_key = {s.key: s for s in blueprint.sections}
    keys = response.slide_keys or [""] * len(response.slide_map)
    checks: dict[int, list[str]] = {}
    for issue in response.issues:
        if issue.slide and issue.level != "info" and issue.message.startswith("check "):
            checks.setdefault(issue.slide, []).append(issue.message[len("check ") :])
    entries = []
    for position, (number, slide_key) in enumerate(zip(response.slide_map, keys), 1):
        detail = slide_key if slide_key and is_detail_key(slide_key) else ""
        section = by_key.get(slide_key) if slide_key and not detail else None
        if section is None:
            section = by_slide.get(number)
        item = {
            "section": section.key if section else f"slide-{number}",
            "title": design.titles.get(section.key, section.title) if section else f"Slide {number}",
            "png": pictures.get(position),
            "text": _slide_text(section, current) if section else "",
            "overflow": overflows.get(position, []),
            "checks": checks.get(position, []),
        }
        if detail:
            spec = entry.manifest.field(stem_of(detail))
            item.update(title=spec.label if spec else stem_of(detail), text=_value_text(current.get(detail) or current.get(stem_of(detail))), detail=detail)
        entries.append(item)
    return entries


def _value_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(" | ".join(str(v) for v in row.values()) for row in value if isinstance(row, dict))
    return str(value or "")


def _refresh_section_pictures(state_key: str, entry, design: Design, store: DesignStore, key: str) -> None:
    blueprint = entry.blueprint
    missing = st.session_state.get(f"{state_key}:missing", "placeholder")
    output, response, keyed = _render_design(entry, store, design, design.brief.subject, design.name, missing)
    keys = response.slide_keys or [""] * len(response.slide_map)
    section = next(s for s in blueprint.sections if s.key == key)
    is_extra = key.startswith("extra_") or key.endswith(WALKTHROUGH_SUFFIX) or key in {e.key for e in active_extras(design.plan)}

    def owned(number: int, k: str) -> bool:
        if is_extra:
            return k == key
        return (not k and number == section.slide) or (is_detail_key(k) and stem_of(k) in section.fields)

    targets = [i for i, (number, k) in enumerate(zip(response.slide_map, keys), 1) if owned(number, k)]
    entries, notes = st.session_state[f"{state_key}:board"]
    pictures: dict[int, bytes] = {}
    overflows: dict[int, list[str]] = {}
    try:
        exported = export_slides(output, output.parent / "png", width=PREVIEW_WIDTH, only=targets, interest=_overflow_interest(entry, response, keyed))
        pictures = {target: f.read_bytes() for target, f in zip(targets, exported.files)}
        for item in exported.overflows:
            overflows.setdefault(item.slide, []).append(item.shape)
        notes = [n for n in notes if not n.startswith("Slide pictures")]
    except RuntimeError as exc:
        logging.getLogger("sdgen.ui").warning("slide picture refresh failed: %s", exc)
        notes = [f"Slide pictures are not available: {exc}."] + [n for n in notes if not n.startswith("Slide pictures")]
    current, _ = _render_fields(design, entry)
    cached = [e for e in entries if e["section"] != key]
    rebuilt: list[dict] = []
    for built in _board_entries(entry, design, response, current, pictures, overflows):
        if built["section"] == key:
            rebuilt.append(built)
            continue
        match = next((e for e in cached if e["section"] == built["section"] and e.get("detail") == built.get("detail")), None)
        if match is not None:
            cached.remove(match)
            rebuilt.append(match)
        else:
            rebuilt.append(built)
    rebuilt.extend(cached)
    st.session_state[f"{state_key}:board"] = (rebuilt, _with_overflow_note(rebuilt, notes))
    stale = set(st.session_state.get(f"{state_key}:stale", set()))
    stale.discard(key)
    st.session_state[f"{state_key}:stale"] = stale


def _overflow_interest(entry, response, keyed: dict[str, str] | None = None) -> dict[int, set[str]]:
    """The filled text boxes on each output slide, so the overflow check ignores untouched template shapes."""
    by_slide: dict[int, set[str]] = {}
    for spec in entry.manifest.fields:
        if spec.kind == "image":
            continue
        for binding in spec.bindings:
            if binding.mode == "replace" and binding.shape.name:
                by_slide.setdefault(binding.slide, set()).add(binding.shape.name)
    keys = response.slide_keys or [""] * len(response.slide_map)
    interest: dict[int, set[str]] = {}
    for position, (number, key) in enumerate(zip(response.slide_map, keys), 1):
        name = (keyed or {}).get(key) if key else None
        interest[position] = {name} if name else by_slide.get(number, set())
    return interest


def _with_overflow_note(entries: list[dict], notes: list[str]) -> list[str]:
    kept = [n for n in notes if not n.startswith("Text overflows") and not n.startswith("Check pass")]
    count = sum(1 for e in entries if e.get("overflow"))
    checked = [e for e in entries if e.get("checks")]
    if count:
        kept.append(f"Text overflows its box on {count} slide(s); the viewer names the boxes in red under those slides.")
    if checked:
        kept.append(f"Check pass: {sum(len(e['checks']) for e in checked)} finding(s) on {len(checked)} slide(s); the viewer lists them under those slides.")
    return kept


def _section_order(design: Design, blueprint: Blueprint) -> list[str]:
    keys = [s.key for s in blueprint.sections]
    ordered = [k for k in design.order if k in keys]
    return ordered + [k for k in keys if k not in ordered]


def _move_section(design: Design, blueprint: Blueprint, key: str, step: int) -> None:
    order = _section_order(design, blueprint)
    index = order.index(key)
    target = index + step
    if 0 <= target < len(order):
        order[index], order[target] = order[target], order[index]
        design.order = order


def _slide_text(section, fields: dict) -> str:
    for key in section.fields:
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text if len(text) <= 140 else text[:140].rstrip() + "…"
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return f"{len(value)} table rows"
    return ""


def _delete_design(store: DesignStore, template: str, name: str) -> None:
    store.delete(name)
    _forget_design(template, name)


def _forget_design(template: str, name: str) -> None:
    for key in [k for k in st.session_state if str(k) == f"design:{template}:{name}" or str(k).startswith(f"design:{template}:{name}:")]:
        del st.session_state[key]
    if f"design_choice:{template}" in st.session_state:
        st.session_state[f"design_choice:{template}"] = NEW_DESIGN
    for key in (f"mapping_design:{template}", "design_remove_choice"):
        st.session_state.pop(key, None)


def _remove_template(reg: Registry, store: DesignStore, target: str) -> None:
    for name in store.names(target):
        store.delete(name)
        _forget_design(target, name)
    reg.remove(target)
    for key in ("template_remove_choice", "design_template"):
        st.session_state.pop(key, None)


def _skipped_sections(design: Design) -> set[str]:
    return set(design.hidden) | {k for k, m in design.modes.items() if m == "blank"}


def _kept_sections(design: Design) -> set[str]:
    return {k for k, m in design.modes.items() if m == "keep"}


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
            spec = manifest.field(key)
            if mode == "keep":
                original = entry.original.fields.get(key) if entry.original else None
                token = spec is not None and any(b.mode == "token" for b in spec.bindings)
                if spec is not None and spec.static:
                    fields.pop(key, None)
                    field_modes[key] = "keep"
                elif token and fields.get(key) not in (None, "", []):
                    pass  # a placeholder token inside a kept slide carries this design's own words
                elif original in (None, "", []):
                    fields.pop(key, None)
                else:
                    fields[key] = original  # the stored deck holds a marker here, so the captured text goes back in
                fields.pop(detail_key(key), None)
            else:
                fields.pop(key, None)
                fields.pop(detail_key(key), None)
                field_modes[key] = "blank"
    return fields, field_modes


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
    empty, ungrounded, short = [], [], []
    for warning in warnings:
        match = EMPTY_FIELD_RE.match(warning)
        if match:
            empty.append(match.group(1))
        elif "not in the brief" in warning:
            ungrounded.append(warning)
        elif " covers " in warning:
            short.append(warning)
        else:
            st.warning(warning)
    if ungrounded:
        st.warning("Not in the brief (check or replace with [TBC]):\n" + "\n".join(f"- {w}" for w in ungrounded))
    if short:
        st.warning("Fewer entries than the brief lists:\n" + "\n".join(f"- {w}" for w in short))
    if empty:
        st.warning(f"{len(empty)} fields came back empty: " + ", ".join(empty))


def _render_design(entry, store: DesignStore, design: Design, subject: str, name: str, missing: str):
    manifest, blueprint = entry.manifest, entry.blueprint
    fields, field_modes = _render_fields(design, entry)
    drawn_flows = store.flows(design)
    extras = active_extras(design.plan) + walkthrough_extras(design, blueprint, manifest, drawn_flows, _section_order(design, blueprint)) + open_questions_extras(design, blueprint, manifest)
    for extra in extras:
        if not extra.generated:
            continue
        if extra.key == OPEN_QUESTIONS_KEY:
            fields[extra.key] = open_question_rows(design.brief.open_questions, extra.columns) if extra.kind == "table" else open_questions_text(design.brief.open_questions)
        else:
            fields[extra.key] = walkthrough_text(drawn_flows[extra.key[: -len(WALKTHROUGH_SUFFIX)]])
    extra_keys = {e.key for e in extras}
    slides_extra = extra_slides(SectionPlan(extras=extras), manifest, blueprint, fields, design.hidden)
    base_manifest = manifest.model_copy(update={"fields": [f for f in manifest.fields if f.key not in extra_keys]})
    plain_blueprint = blueprint.model_copy(update={"sections": [s for s in blueprint.sections if s.key not in extra_keys]})
    details = detail_prototypes(plain_blueprint, base_manifest)
    store.apply_reference_pictures(design, {s.key: k for s in plain_blueprint.sections for k in s.fields if base_manifest.field(k) is not None and base_manifest.field(k).kind == "image"})
    fields = {k: v for k, v in fields.items() if k not in extra_keys}
    images = {k: v for k, v in store.content(design, base_manifest).fields.items() if base_manifest.field(k) and base_manifest.field(k).kind == "image"}
    final = Content(globals=_globals(subject), fields={**fields, **images})
    sections = {s.key: s for s in blueprint.sections if s.key not in extra_keys}
    flows = {}
    for section_key, flow in drawn_flows.items():
        section = sections.get(section_key)
        for key in (section.fields if section else []):
            if base_manifest.field(key) is not None and base_manifest.field(key).kind == "image":
                flows[key] = flow
    hidden_slides = [sections[k].slide for k in design.hidden if k in sections]
    slide_order = [sections[k].slide for k in _section_order(design, blueprint) if k in sections] if design.order else []
    titles = {sections[k].slide: t for k, t in design.titles.items() if k in sections and t.strip()}
    out_dir = Path(tempfile.mkdtemp(prefix="sdgen-out-"))
    output = out_dir / f"{slugify(subject) or name}.pptx"
    response = render_document(
        RenderRequest(
            template=str(entry.template_path),
            manifest=base_manifest,
            content=final,
            output=str(output),
            missing=missing,
            continue_on=continuation_slides(blueprint),
            field_modes=field_modes,
            hidden_slides=hidden_slides,
            slide_order=slide_order,
            titles=titles,
            extras=slides_extra,
            flows=flows,
            clear_shapes=[(c.slide, c.shape) for c in (design.plan.clear if design.plan else []) if c.include],
            subject_slides=[s.slide for s in blueprint.sections if s.kind == "cover"],
            details=details,
        )
    )
    keyed = {e.key: (e.spec.bindings[0].shape.name or "") for e in slides_extra if e.spec.bindings}
    keyed.update({spec.key: (spec.bindings[0].shape.name or "") for spec in details.values() if spec.bindings})
    return output, response, keyed


def _run_draft(state_key: str, entry, design: Design, store: DesignStore, subject: str, version: int, provider: str, settings: dict) -> None:
    manifest, blueprint = entry.manifest, entry.blueprint
    try:
        llm = get_llm(provider, **settings)
        if design.mapping is not None and not design.brief.mapping_summary.strip():
            design.brief.mapping_summary = _mapping_note(design)
        with st.spinner(f"Writing the slides with {provider}. This can take a minute."):
            result = draft_content(design.brief, blueprint, manifest, llm, original=entry.original, skip_sections=set(design.hidden) | set(design.modes))
    except (LLMNotConfigured, LLMError) as exc:
        st.error(str(exc))
        return
    if not result.content.fields:
        st.error("The reply did not use the expected section headings, so nothing was filled. Try again or choose another model.")
        return
    current = load_markdown(design.content_markdown, manifest) if design.content_markdown.strip() else Content()
    last = load_markdown(design.last_draft, manifest) if design.last_draft.strip() else Content()
    merged = _merge_draft(current.fields, last.fields, result.content.fields)
    design.content_markdown = dump_markdown(Content(globals=_globals(subject), fields=merged), manifest)
    design.last_draft = result.markdown
    design.llm = result.llm
    store.save(design)
    total = sum(len(s["fields"]) for s in writable_sections(blueprint, manifest))
    extra = f", {len(result.mechanical)} filled from facts and template" if result.mechanical else ""
    waiting = _pending_flow_sections(design, entry, store)
    if waiting:
        st.session_state[f"{state_key}:draw_after_write"] = True
        extra += f"; {len(waiting)} diagram(s) wait for your format choice"
    drafted = sum(1 for k in result.content.fields if not is_detail_key(k))
    st.session_state[f"{state_key}:draft_done"] = f"Drafted {drafted} of {total} fields with {result.llm}{extra}. Review them in step 5, then generate."
    st.session_state[f"{state_key}:draft_warnings"] = result.warnings
    st.session_state[f"{state_key}:v"] = version + 1
    st.rerun()


def _pending_flow_sections(design: Design, entry, store: DesignStore) -> list:
    """Diagram sections the plan wants drawn that have neither a drawing nor an uploaded image yet."""
    if design.plan is None or not design.plan.flows:
        return []
    existing = store.flows(design)
    sections = {s.key: s for s in entry.blueprint.sections}
    pending = []
    for request in design.plan.flows:
        section = sections.get(request.section)
        if section is None or request.section in existing or request.section in design.hidden:
            continue
        if any(design.images.get(k) for k in section.fields):
            continue
        pending.append(section)
    return pending


def _current_llm(state_key: str):
    provider, settings = st.session_state.get("llm", ("mock", {}))
    settings = dict(settings)
    chosen = st.session_state.get(f"{state_key}:deployment")
    if chosen:
        settings["model"] = chosen
    return get_llm(provider, **settings)


def _merge_draft(current: dict, last: dict, fresh: dict) -> dict:
    """A new draft replaces only fields the user has not edited since the previous draft."""
    merged = dict(current)
    for key, value in fresh.items():
        existing = current.get(key)
        if existing in (None, "", []) or existing == last.get(key):
            merged[key] = value
    return merged


def _material_editor(state_key: str, prefix: str, version: int, design: Design, store: DesignStore, blueprint, provider: str, settings: dict, image_fields: dict[str, str] | None = None) -> None:
    """The reference material of the brief: existing items with their editable text, then the form that adds pictures, text or a link."""
    options = [s.key for s in blueprint.sections if s.kind not in ("cover", "static", "divider") and not s.generated and not s.key.startswith("extra_")]
    names = {s.key: design.titles.get(s.key, s.title) for s in blueprint.sections}
    image_fields = image_fields or {}
    for item in design.brief.material:
        with st.container(border=True):
            col_text, col_meta = st.columns([3, 2])
            with col_text:
                st.markdown(f"**{item.label}** ({item.kind})")
                item.text = st.text_area("Text the model reads", key=_init(f"{prefix}mat:text:{item.id}", item.text), height=140, label_visibility="collapsed", placeholder="No text yet.")
            with col_meta:
                st.caption(" · ".join(x for x in (item.status or "added", item.added[:10], item.url, item.file) if x))
                if item.note:
                    st.caption(f"Note: {item.note}")
                item.tags = st.multiselect("For these slides (empty: all)", options, key=_init(f"{prefix}mat:tags:{item.id}", [t for t in item.tags if t in options]), format_func=lambda key: names.get(key, key))
                if item.kind == "image" and item.file and image_fields:
                    slots = [""] + list(image_fields)
                    current = next((f.section for f in (design.plan.flows if design.plan else []) if f.material_id == item.id), "")
                    chosen = st.selectbox("Use as the picture for", slots, index=slots.index(current) if current in slots else 0, format_func=lambda key: names.get(key, key) if key else "no slide, the model only reads it", key=f"{prefix}mat:use:{item.id}")
                    if chosen != current:
                        _set_reference_picture(design, store, blueprint, chosen or current, item.id if chosen else "", image_fields)
                        _refresh_material(state_key, design, store, version)
                col_redo, col_remove = st.columns(2)
                with col_redo:
                    if item.kind == "image" and st.button("Transcribe again", key=f"{state_key}:mat:redo:{item.id}"):
                        path = store.material_path(design, item)
                        _transcribe(item, path.read_bytes() if path is not None and path.is_file() else b"", provider, settings)
                        _refresh_material(state_key, design, store, version)
                    if item.kind == "link" and st.button("Fetch again", key=f"{state_key}:mat:redo:{item.id}"):
                        title, text, item.status = materials.fetch_link(item.url)
                        item.text, item.title = text or item.text, item.title or title
                        _refresh_material(state_key, design, store, version)
                with col_remove:
                    if st.button("Remove", key=f"{state_key}:mat:rm:{item.id}"):
                        store.remove_material(design, item.id)
                        _refresh_material(state_key, design, store, version)
    files = st.file_uploader("Pictures (PNG, JPG) or text files (Markdown, Mermaid, draw.io, CSV, XML, JSON)", type=[*materials.IMAGE_TYPES, *materials.TEXT_TYPES], accept_multiple_files=True, key=f"{prefix}mat:files")
    pasted = st.text_area("Or paste text: notes, Markdown, Mermaid", key=f"{prefix}mat:paste", height=100)
    link = st.text_input("Or a link", key=f"{prefix}mat:link", placeholder="https://", help="Public pages are fetched once and kept as text. Pages behind a login cannot be read: paste the relevant part as text instead.")
    col_title, col_note = st.columns(2)
    with col_title:
        title = st.text_input("Title", key=f"{prefix}mat:title", placeholder="optional")
    with col_note:
        note = st.text_input("What it shows or why it matters", key=f"{prefix}mat:note", placeholder="optional")
    tags = st.multiselect("For these slides (empty: all)", options, key=f"{prefix}mat:newtags", format_func=lambda key: names.get(key, key))
    nothing = not files and not pasted.strip() and not link.strip()
    if st.button("Add to the brief", key=f"{state_key}:mat:add", disabled=nothing):
        added = _add_material(design, store, files or [], pasted, link, title, note, tags, provider, settings)
        st.session_state[f"{state_key}:mat_note"] = "Added: " + ", ".join(added) + "." if added else "Nothing added."
        _refresh_material(state_key, design, store, version)
    if st.session_state.get(f"{state_key}:mat_note"):
        st.info(st.session_state.pop(f"{state_key}:mat_note"))


def _add_material(design: Design, store: DesignStore, files: list, pasted: str, link: str, title: str, note: str, tags: list[str], provider: str, settings: dict) -> list[str]:
    added: list[str] = []
    for file in files:
        name, data = file.name, file.getvalue()
        if Path(name).suffix.lower().lstrip(".") in materials.IMAGE_TYPES:
            item = store.add_material(design, materials.new_material("image", title=title or Path(name).stem, note=note, tags=tags), data, file_name=name)
            _transcribe(item, data, provider, settings)
        else:
            item = store.add_material(design, materials.new_material("text", title=title or Path(name).stem, note=note, tags=tags, text=materials.text_from_upload(name, data), status="uploaded"), data, file_name=name)
        added.append(item.label)
    if pasted.strip():
        added.append(store.add_material(design, materials.new_material("text", title=title or "Pasted text", note=note, tags=tags, text=pasted.strip(), status="pasted")).label)
    if link.strip():
        page_title, text, status = materials.fetch_link(link)
        added.append(store.add_material(design, materials.new_material("link", title=title or page_title or link.strip(), note=note, tags=tags, url=link.strip(), text=text, status=status)).label)
    return added


def _transcribe(item: materials.Material, data: bytes, provider: str, settings: dict) -> None:
    if provider == "mock":
        item.status = "not transcribed (mock provider): pick an AI provider and press Transcribe again"
        return
    try:
        llm = get_llm(provider, **settings)
        with st.spinner(f"Reading {item.label}."):
            item.text = materials.describe_image(llm, data, materials.mime_of(item.file), hint=" ".join(x for x in (item.title, item.note) if x))
        item.status = f"transcribed with {getattr(llm, 'label', llm.name)} on {materials.now()[:10]}"
    except (LLMNotConfigured, LLMError, ValueError) as exc:
        item.status = f"transcription failed: {exc}"


def _split_field(state_key: str, design: Design, store: DesignStore, key: str, provider: str, settings: dict, version: int) -> None:
    """Breaks a field pasted as one line into one entry per line and reloads the brief widgets."""
    fact = key[len(FACT_PREFIX) :] if key.startswith(FACT_PREFIX) else ""
    if fact:
        spec = FACTS_BY_KEY[fact]
        text, cells, label, guidance = design.brief.facts.get(fact, ""), FACT_CELLS[fact], spec.label, spec.guidance
    else:
        label, guidance = next((field_label, field_guidance) for field_key, field_label, field_guidance in BRIEF_FIELDS if field_key == key)
        text, cells = getattr(design.brief, key), SEPARATED_FIELDS[key]
    try:
        llm = get_llm(provider, **settings)
        with st.spinner(f"Splitting {label}."):
            result = resplit_lines(llm, label, text, cells, guidance)
    except (LLMNotConfigured, LLMError):
        result = split_joined(text, cells)
    if fact:
        design.brief.facts[fact] = result
    else:
        setattr(design.brief, key, result)
    st.session_state[f"{state_key}:brief_note"] = f"{label}: split into {len(result.splitlines())} lines."
    _refresh_material(state_key, design, store, version)


def _set_reference_picture(design: Design, store: DesignStore, blueprint: Blueprint, section_key: str, material_id: str, image_fields: dict[str, str]) -> None:
    """Points the plan's flow request of a diagram section at a reference picture (or clears it) and updates the image slot."""
    if design.plan is None:
        design.plan = default_plan(blueprint, {k for k, names in design.images.items() if names})
    if material_id:
        for request in design.plan.flows:
            if request.material_id == material_id and request.section != section_key:
                request.material_id = ""  # one slide per picture
    request = next((f for f in design.plan.flows if f.section == section_key), None)
    if request is None:
        section = blueprint.section(section_key)
        request = FlowRequest(section=section_key, title=design.titles.get(section_key, section.title if section else section_key), purpose=section.ask if section else "")
        design.plan.flows.append(request)
    request.material_id = material_id
    store.apply_reference_pictures(design, image_fields)
    store.save(design)


def _refresh_material(state_key: str, design: Design, store: DesignStore, version: int) -> None:
    store.save(design)
    st.session_state[f"{state_key}:v"] = version + 1
    st.rerun()


def _draw_flows_for(state_key: str, design: Design, store: DesignStore, sections: list, requests: dict, provider: str, settings: dict) -> None:
    try:
        llm = get_llm(provider, **settings)
        planner = llm.with_effort("medium") if hasattr(llm, "with_effort") else llm
        wanted = [requests.get(s.key) or FlowRequest(section=s.key, title=design.titles.get(s.key, s.title), purpose=s.ask) for s in sections]
        wanted = _sized_requests(design, sections, wanted)
        with st.spinner("Designing the diagrams."):
            specs = plan_flows(design.brief, wanted, planner, icons=_flow_icons(design), images=store.material_images(design, {s.key for s in sections}))
    except (LLMNotConfigured, LLMError) as exc:
        st.error(str(exc))
        return
    layout = st.session_state.get(f"{state_key}:fmt:layout", "bands")
    for key, spec in specs.items():
        chosen = spec.layout if spec.layout == "sequence" or layout not in LAYOUT_NAMES else layout  # a detected handshake keeps its sequence
        store.save_flow(design, key, spec.model_copy(update={"layout": chosen}))
    missing = [design.titles.get(s.key, s.title) for s in sections if s.key not in specs]
    checks = [f"{design.titles.get(key, key)}: {problem}" for key, spec in specs.items() for problem in lane_mismatches(spec)]
    note = f"Drew {len(specs)} diagram(s) from the brief." + (f" The model returned no flow for: {', '.join(missing)}." if missing else "")
    st.session_state[f"{state_key}:flow_note"] = note + (" Check the lanes: " + "; ".join(checks) + "." if checks else "")
    st.rerun()


def _extract_systems(state_key: str, design: Design, store: DesignStore, provider: str, settings: dict, version: int) -> None:
    """Fills the systems fact from the brief text, so the diagrams get their lanes."""
    try:
        llm = get_llm(provider, **settings)
        with st.spinner("Reading the systems from the brief."):
            found = extract_facts(design.brief, [FactQuestion(spec=FACTS_BY_KEY["systems"])], llm)
    except (LLMNotConfigured, LLMError) as exc:
        st.error(str(exc))
        return
    if not found.get("systems"):
        st.session_state[f"{state_key}:flow_note"] = "No systems found in the brief text; type them under 'Systems in the flow' in the Brief step."
        st.rerun()
    design.brief.facts["systems"] = found["systems"]
    st.session_state[f"{state_key}:flow_note"] = "Systems taken from the brief: " + "; ".join(lane.title for lane in system_lanes(design.brief)) + ". Check them under 'Systems in the flow' in the Brief step."
    _refresh_material(state_key, design, store, version)


def _sized_requests(design: Design, sections: list, requests: list) -> list:
    """Each request learns the size of its image slot, so the model draws as many nodes as fit."""
    try:
        entry = Registry(os.environ.get("SDGEN_TEMPLATES", str(ROOT / "templates"))).load(design.template)
        sizes = slot_sizes(entry)
    except Exception:
        return requests
    sized = []
    for section, request in zip(sections, requests):
        size = next((sizes[k] for k in section.fields if k in sizes), None)
        sized.append(request.model_copy(update={"width_in": size[0], "height_in": size[1]}) if size else request)
    return sized


def slot_sizes(entry) -> dict[str, tuple[float, float]]:
    """Width and height in inches of every image slot of the template, by field key."""
    prs = Presentation(str(entry.template_path))
    slides = list(prs.slides)
    sizes: dict[str, tuple[float, float]] = {}
    for spec in entry.manifest.fields:
        if spec.kind != "image":
            continue
        for binding in spec.bindings:
            if 1 <= binding.slide <= len(slides):
                shape = find_shape(slides[binding.slide - 1], binding.shape.id)
                if shape is not None and shape.width and shape.height:
                    sizes[spec.key] = (round(shape.width / 914400, 2), round(shape.height / 914400, 2))
                    break
    return sizes


def _icon_note(design: Design) -> str:
    """What the icon set does for the drawings of this design."""
    if not uses_sap(design.brief):
        return "Nodes draw as plain shapes: the brief is not about SAP, so no icon set is used."
    have = installed_keys()
    total = len(icon_keys())
    if not have:
        return "No SAP icon files are installed, so every node draws as a plain shape. Run sdgen icons --fetch once (see assets/icons/README.md)."
    if len(have) == total:
        return f"Every node gets an icon: all {total} SAP and non-SAP icons of the catalogue are installed."
    return f"Icons installed for {len(have)} of {total} catalogue entries; the other nodes draw as plain shapes until sdgen icons --fetch has run."


def _show_icon_note(design: Design) -> None:
    note = _icon_note(design)
    (st.warning if note.startswith("No SAP icon") else st.caption)(note)


def _flow_icons(design: Design) -> list[str] | None:
    """The icon catalogue when the brief is about SAP; the user is never asked."""
    return icon_keys() if uses_sap(design.brief) else None


@st.dialog("Draw diagrams", width="medium")
def _diagram_format_dialog(state_key: str, sections: list, requests: dict, provider: str, settings: dict) -> None:
    design: Design = st.session_state[state_key]
    store = design_store()
    names = ", ".join(design.titles.get(s.key, s.title) for s in sections)
    st.caption(f"Diagrams: {names}. The slide always gets editable PowerPoint shapes, whatever the format; draw.io and Mermaid add a file next to the design that you can open, adjust, export as PNG and upload in step 4 to replace the shapes. Not now keeps the slots empty; step 4 draws them later.")
    _show_icon_note(design)
    current = design.diagram_format if design.diagram_format in DIAGRAM_FORMATS else "shapes"
    choice = st.radio("Format", list(DIAGRAM_FORMATS), format_func=DIAGRAM_FORMATS.get, index=list(DIAGRAM_FORMATS).index(current), key=f"{state_key}:fmt:choice")
    st.radio("Layout", list(LAYOUT_NAMES), format_func=LAYOUT_NAMES.get, horizontal=True, key=_init(f"{state_key}:fmt:layout", "bands"), help="System bands read left to right and wrap when the steps do not fit; lane columns stack the steps of each system.")
    everywhere = st.checkbox("Use this format for all diagrams of this design", value=True, key=f"{state_key}:fmt:all")
    col_go, col_later = st.columns([1, 1])
    with col_go:
        go = st.button("Draw", type="primary", key=f"{state_key}:fmt:go")
    with col_later:
        if st.button("Not now", key=f"{state_key}:fmt:later"):
            _go_to(state_key, "diagrams")
    if go:
        if everywhere:
            design.diagram_format = choice
            design.diagram_formats = {}
        else:
            for section in sections:
                design.diagram_formats[section.key] = choice
        store.save(design)
        _draw_flows_for(state_key, design, store, sections, requests, provider, settings)


def _write_title(design: Design) -> str:
    return "3. Write the slides" + (f": written with {design.llm}" if design.llm and design.content_markdown.strip() else "")


def _review_title(sections: list[tuple], content: Content) -> str:
    written, total = _review_progress(sections, content)
    return f"5. Review sections: {written} of {total} written"


def _review_progress(sections: list[tuple], content: Content) -> tuple[int, int]:
    written = sum(1 for _, fields in sections if any(content.fields.get(f.key) not in (None, "", []) for f in fields))
    return written, len(sections)


def _current_step(state_key: str, design: Design, present: list[str]) -> str:
    """The step shown open: the first one not marked complete, until a button on the page moves it."""
    key = f"{state_key}:step"
    if st.session_state.get(key) not in present:
        st.session_state[key] = next((s for s in present if s not in design.completed), present[-1])
    return st.session_state[key]


def _expander_key(state_key: str, name: str, step: str) -> str:
    """The key changes with the open state, so the browser applies it; manual toggles stay untouched otherwise."""
    return f"{state_key}:exp:{name}:{'open' if step == name else 'shut'}"


def _go_to(state_key: str, step: str) -> None:
    st.session_state[f"{state_key}:step"] = step
    st.rerun()


def _complete_section(state_key: str, design: Design, store: DesignStore, step: str, present: list[str]) -> None:
    """Marks the step done and opens the next one; a done step can be reopened."""
    if step in design.completed:
        col_note, col_open = st.columns([3, 1], vertical_alignment="center")
        col_note.caption("Section completed.")
        if col_open.button("Reopen section", key=f"{state_key}:reopen:{step}"):
            design.completed.remove(step)
            store.save(design)
            _go_to(state_key, step)
        return
    if st.button("Complete section", key=f"{state_key}:complete:{step}", help="Marks this step done and opens the next one"):
        design.completed.append(step)
        store.save(design)
        following = present[present.index(step) + 1:] if step in present else []
        _go_to(state_key, following[0] if following else step)


def _done(finished: bool) -> str | None:
    return DONE_ICON if finished else None


def _plan_title(design: Design) -> str:
    if design.plan is None:
        return "2. Section plan"
    hidden = len(design.hidden)
    retitled = len(design.titles)
    extras = len([e for e in design.plan.extras if e.include])
    return f"2. Section plan: {hidden} hidden, {retitled} retitled, {extras} extra slide(s), {len(design.plan.flows)} diagram(s) to draw"


def _plan_editor(plan: SectionPlan, blueprint: Blueprint, key: str, pictures: dict[str, str] | None = None) -> SectionPlan:
    sections = {s.key: s for s in blueprint.sections}
    rows = []
    for decision in plan.decisions:
        section = sections.get(decision.key)
        rows.append(
            {
                "use": decision.use,
                "slide": section.slide if section else 0,
                "template title": section.title if section else decision.key,
                "title": decision.title,
                "source": decision.source,
                "reason": decision.reason,
                "key": decision.key,
            }
        )
    frame = pd.DataFrame(rows, columns=["use", "slide", "template title", "title", "source", "reason", "key"])
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        height=min(60 + 36 * len(frame), 640),
        disabled=["slide", "template title", "reason", "key"],
        key=f"{key}:grid",
        column_config={
            "use": st.column_config.CheckboxColumn("Use"),
            "slide": st.column_config.NumberColumn("Slide"),
            "template title": st.column_config.TextColumn("Template title"),
            "title": st.column_config.TextColumn("Title for this design", help="Leave empty to keep the template title."),
            "source": st.column_config.SelectboxColumn("Source", options=list(SOURCES), required=True),
            "reason": st.column_config.TextColumn("Reason", width="large"),
            "key": None,
        },
    )
    decisions = []
    for _, row in edited.iterrows():
        decisions.append(
            SectionDecision(
                key=str(row["key"]),
                use=bool(row["use"]),
                title=_text(row["title"]),
                source=str(row["source"]) if str(row["source"]) in SOURCES else "draft",
                reason=_text(row["reason"]),
            )
        )
    extras = []
    if plan.extras:
        st.markdown("**Extra slides**")
        for extra in plan.extras:
            label = f"{extra.title} ({extra.kind}" + (": " + ", ".join(extra.columns) if extra.columns else "") + ")" + (f". {extra.reason}" if extra.reason else "")
            include = st.checkbox(label, value=extra.include, key=f"{key}:extra:{extra.key}")
            extras.append(extra.model_copy(update={"include": include}))
    flows = []
    if plan.flows:
        st.markdown("**Diagrams to draw from the brief**")
        seen: set[str] = set()
        for flow in plan.flows:
            if flow.section in seen:
                continue  # an older saved plan may still hold a duplicate
            seen.add(flow.section)
            st.caption(f"{flow.title or flow.section}: {flow.purpose}" if flow.purpose else flow.title or flow.section)
            if pictures:
                options = [""] + list(pictures)
                choice = st.selectbox("Picture from the reference material instead of a drawing", options, index=options.index(flow.material_id) if flow.material_id in options else 0, format_func=lambda item_id: pictures.get(item_id, "none, draw it") if item_id else "none, draw it", key=f"{key}:flow_pic:{flow.section}")
                flows.append(flow.model_copy(update={"material_id": choice}))
            else:
                flows.append(flow)
    clear = []
    if plan.clear:
        st.markdown("**Template text to clear** (wording from the earlier project that no field replaces)")
        for item in plan.clear:
            label = f"Slide {item.slide}: {item.text[:90]}" + (f" ({item.reason})" if item.reason else "")
            include = st.checkbox(label, value=item.include, key=f"{key}:clear:{item.slide}:{item.shape}")
            clear.append(item.model_copy(update={"include": include}))
    return plan.model_copy(update={"decisions": decisions, "extras": extras, "flows": flows, "clear": clear})


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
