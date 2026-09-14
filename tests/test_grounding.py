from sdgen.brief import Brief
from sdgen.content import Content
from sdgen.grounding import GROUNDING_RULE, corpus, coverage_warnings, entry_count, grounding_warnings, ungrounded_terms, written_count
from sdgen.manifest import FieldSpec, Manifest

BRIEF = Brief(
    subject="Lockbox via SWIFT",
    about="Lockbox files reach S/4HANA on RISE through SAP BTP Integration Suite; the VM runs AutoClient.",
    approach="BTP polls reception/Inbound/<bank> every 15 minutes and writes to <bank>/IN with a temp name.",
    acceptance_criteria=(
        "Lockbox inbound (per bank: BoA, JPMC, PNC)\n\n"
        "Happy path: the file lands in <bank>/IN within 15 minutes.\n"
        "Duplicate: the same file is not transferred twice.\n"
        "Bank statements inbound 3. Happy path as above. 4. Duplicate statement blocked.\n"
        "Cross-cutting 5. Least privilege verified on both doors."
    ),
    decisions_log="File based transport | Programme | decided\nBTP is the only client | Architecture | decided\nOne IN folder per bank | FI | proposed",
    operations="Failure\tWhat happens\tAlert to\nLogin fails\tretry 3 times\tSupport\nHost key mismatch\tdrop\tSupport\n\nMonitoring\nOne message per file.",
)


def test_identifiers_absent_from_the_brief_are_flagged():
    known = corpus(BRIEF)
    text = "Schedule RFEBLB00 on sftp.example.com under /interfaces/bank; SAP BTP writes to <bank>/IN via AutoClient and S/4HANA."
    assert ungrounded_terms(text, known) == ["RFEBLB00", "sftp.example.com", "/interfaces/bank"]
    assert ungrounded_terms("Plain words, back-off and 15 minutes only.", known) == []


def test_tbc_markers_labels_and_reference_ids_are_ignored():
    known = corpus(BRIEF)
    text = "[TBC: which host] GAP-APP-01 INT-IN-02 Business_Need n/a OneERP"
    assert ungrounded_terms(text, known) == ["Business_Need", "OneERP"]
    assert ungrounded_terms(text, known, ignore={"Business_Need"}) == ["OneERP"]


def test_grounding_warnings_name_the_field():
    manifest = Manifest(name="m", fields=[FieldSpec(key="need", label="Business Need"), FieldSpec(key="scope", label="Scope", kind="table", columns=["Function", "Countries"])])
    content = Content(fields={"need": "Post via FF_5 and FEBP.", "scope": [{"Function": "Lockbox", "Countries": "ZA"}]})
    assert grounding_warnings(content, BRIEF, manifest) == ["field 'need' (Business Need) names things not in the brief: FF_5, FEBP"]


def test_coverage_compares_entry_counts():
    assert entry_count(BRIEF.acceptance_criteria) == 5
    assert entry_count(BRIEF.decisions_log) == 3
    assert entry_count(BRIEF.operations) == 2
    assert entry_count("") == 0
    manifest = Manifest(
        name="m",
        fields=[
            FieldSpec(key="extra_acceptance_criteria", label="Acceptance criteria", kind="table", columns=["Ref", "Scenario"]),
            FieldSpec(key="extra_decisions", label="Decisions log", kind="table", columns=["Ref", "Decision"]),
            FieldSpec(key="ops", label="Operations and runbook", kind="bullets"),
        ],
    )
    content = Content(
        fields={
            "extra_acceptance_criteria": [{"Ref": "1", "Scenario": "Happy path"}, {"Ref": "2", "Scenario": "Duplicate"}],
            "extra_decisions": [{"Ref": "1", "Decision": "File based"}, {"Ref": "2", "Decision": "BTP only"}, {"Ref": "3", "Decision": "One IN"}],
            "ops": "- Login fails: retry.\n- Host key mismatch: drop.",
        }
    )
    assert coverage_warnings(content, BRIEF, manifest) == ["field 'extra_acceptance_criteria' (Acceptance criteria) covers 2 of 5 entries of the brief's acceptance criteria and test scenarios"]
    assert written_count([{"Ref": "[To be completed]"}]) == 0 and written_count("a\n\nb") == 2


def test_every_system_prompt_carries_the_grounding_rule():
    from sdgen import flow, plan, writer

    assert GROUNDING_RULE in writer.SYSTEM_PROMPT and GROUNDING_RULE in plan.SYSTEM_PROMPT and GROUNDING_RULE in flow.SYSTEM_PROMPT
    assert "[TBC: what to ask]" in GROUNDING_RULE and "one row per entry of that list, never fewer" in writer.SYSTEM_PROMPT


def test_joined_words_and_placeholder_patterns_are_judged_by_their_parts():
    known = corpus(BRIEF)
    assert ungrounded_terms("Lockbox/Duplicate paths, reception/Inbound/<bank>/<name> and <bank>_<seq>.lockbox", known) == []
    assert ungrounded_terms("RFEBLB00/OB10 and PGP/SSH", known) == ["RFEBLB00/OB10", "PGP/SSH"]
    manifest = Manifest(name="m", fields=[FieldSpec(key="need", label="Business Need")])
    assert grounding_warnings(Content(fields={"need": "Post via FF_5."}), BRIEF, manifest, skip={"need"}) == []
