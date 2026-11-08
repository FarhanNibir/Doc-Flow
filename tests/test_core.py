import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core import pipeline as P
from core.ai import MockProvider, draft_section, validate_output
from core.schemas import SectionOutput, Template
from core.workflow import WorkflowError

HERE = os.path.dirname(os.path.dirname(__file__))
DATA = {
    "organization": "Northfield Business Center", "asset": "AC-204",
    "inspection_date": "2026-11-06", "inspector": "Arif Rahman",
    "findings": "Dusty filter. Cooling slightly reduced.",
    "recommended_action": "Clean filter and recheck.",
}


@pytest.fixture
def t():
    return P.load_template(os.path.join(HERE, "templates", "inspection_report.json"))


def ready_for_review(t, tmp_path, data=DATA):
    req = P.start_request(t, "R1", dict(data), MockProvider())
    P.layout_check(t, req, str(tmp_path))
    P.internal_qa_pass(req, "staff")
    return req


def test_missing_required_field_goes_to_needs_info(t):
    d = dict(DATA, inspector="")
    req = P.start_request(t, "R0", d, MockProvider())
    assert req.state == "NEEDS_INFO"


def test_template_rejects_bad_zone_reference():
    bad = json.load(open(os.path.join(HERE, "templates", "inspection_report.json")))
    bad["sections"][0]["zone"] = "nope"
    with pytest.raises(Exception):
        Template.model_validate(bad)


def test_validator_blocks_invented_numbers(t):
    spec = next(s for s in t.sections if s.id == "observations")
    out = SectionOutput(text="Temperature was 45 C", source_fields=["findings"])
    assert any("45" in p for p in validate_output(out, spec, DATA))


def test_validator_blocks_out_of_mapping_fields(t):
    spec = next(s for s in t.sections if s.id == "observations")
    out = SectionOutput(text="ok", source_fields=["inspector"])
    assert validate_output(out, spec, DATA)


def test_bad_provider_output_is_flagged_for_human(t):
    class Bad:
        name = "bad"
        def complete_json(self, s, u): return "not json"
    spec = next(s for s in t.sections if s.id == "summary")
    r = draft_section(Bad(), spec, DATA)
    assert "needs human review" in r.flags and r.attempts == 2


def test_copy_sections_are_verbatim(t):
    spec = next(s for s in t.sections if s.id == "identification")
    r = draft_section(MockProvider(), spec, DATA)
    assert "Arif Rahman" in r.text and "AC-204" in r.text


def test_ai_only_gets_mapped_fields(t):
    seen = {}
    class Spy(MockProvider):
        def complete_json(self, s, u):
            seen.update(json.loads(u)["fields"]); return super().complete_json(s, u)
    spec = next(s for s in t.sections if s.id == "observations")
    draft_section(Spy(), spec, DATA)
    assert set(seen) == {"findings"}


def test_cannot_approve_before_review(t):
    req = P.start_request(t, "R2", dict(DATA), MockProvider())
    with pytest.raises(WorkflowError):
        req.approve("Mahmud Karim")


def test_cannot_sign_unapproved(t, tmp_path):
    req = ready_for_review(t, tmp_path)
    with pytest.raises(WorkflowError):
        P.sign_and_finalize(t, req, str(tmp_path), "Mahmud Karim")


def test_edit_after_approval_invalidates_and_blocks_signing(t, tmp_path):
    req = ready_for_review(t, tmp_path)
    req.approve("Mahmud Karim")
    P.edit_section(t, req, "summary", "Changed after approval.", "staff")
    assert req.state == "REVIEW_PENDING"
    with pytest.raises(WorkflowError):
        P.sign_and_finalize(t, req, str(tmp_path), "Mahmud Karim")


def test_full_happy_path_and_locking(t, tmp_path):
    req = ready_for_review(t, tmp_path)
    req.approve("Mahmud Karim")
    path, digest = P.sign_and_finalize(t, req, str(tmp_path), "Mahmud Karim")
    P.deliver(req)
    assert os.path.getsize(path) > 1000 and len(digest) == 64
    assert req.state == "DELIVERED" and req.verify_audit()
    with pytest.raises(WorkflowError):
        req.add_version({}, {}, t.id, 1, [], "x")


def test_request_roundtrip_keeps_approval_and_audit(t, tmp_path):
    from core.workflow import Request
    req = ready_for_review(t, tmp_path)
    req.approve("Mahmud Karim")
    back = Request.from_dict(json.loads(json.dumps(req.to_dict())))
    assert back.state == "SIGNING_PENDING" and back.verify_audit()
    back.assert_signable()
    assert back.current.approved_by == "Mahmud Karim"


def test_audit_tamper_detected(t, tmp_path):
    req = ready_for_review(t, tmp_path)
    req.audit[1]["actor"] = "attacker"
    assert not req.verify_audit()


def test_overflow_continues_on_new_page_not_silently_shrunk(t, tmp_path):
    d = dict(DATA, findings=" ".join(f"Observation {i} checked." for i in range(1, 250)))
    t.sections[2].max_words = 2000
    req = ready_for_review(t, tmp_path, d)
    from core.layout import Renderer
    r = Renderer(t, str(tmp_path / "x.pdf"))
    r.render(d, req.current.sections, header={}, status_line="")
    rep = r.save()
    assert rep.pages >= 2 and "observations" in rep.overflowed and rep.ok
