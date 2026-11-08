"""High-level operations that tie AI drafting, layout, workflow and signing together."""
from __future__ import annotations
import hashlib
import json
import os

from .ai import Provider, draft_document, regenerate_section
from .layout import LayoutReport, Renderer
from .schemas import Template
from .workflow import Request, WorkflowError


def load_template(path: str) -> Template:
    with open(path, encoding="utf-8") as f:
        return Template.model_validate(json.load(f))


def validate_intake(template: Template, data: dict) -> list[str]:
    """Return a list of problems; non-empty means the request goes to NEEDS_INFO."""
    problems = []
    for f in template.fields:
        if f.required and not str(data.get(f.name, "")).strip():
            problems.append(f"missing required field: {f.label}")
    return problems


def _render(template: Template, req: Request, out_path: str, *, final: bool, reviewer: str | None):
    v = req.current
    r = Renderer(template, out_path)
    status = (f"{req.id} · v{v.number} · {'FINAL' if final else 'DRAFT — NOT APPROVED'} · "
              f"hash {v.hash[:12]}")
    r.render(v.data, v.sections,
             header={"title": template.name, "subtitle": "Illustrative record — DEMO ONLY"},
             status_line=status, guides=not final,
             watermark=None if final else "DRAFT")
    if final:
        # signature + seal are drawn ONLY inside their reserved zones, ONLY after approval
        sig = next(z for z in template.zones if z.kind == "signature")
        seal = next(z for z in template.zones if z.kind == "seal")
        r.stamp(sig.id, [reviewer.split()[0][0] + ". " + reviewer.split()[-1],
                         f"{reviewer} · Authorized Reviewer",
                         f"Approved {v.hash[:12]}"], "signature")
        r.stamp(seal.id, ["ORGANIZATION", "REVIEWED", "DEMO SEAL"], "seal")
    return r.save()


def start_request(template: Template, req_id: str, data: dict, provider: Provider,
                  actor: str = "client") -> Request:
    req = Request(req_id)
    req.log(actor, "submitted")
    req.move("VALIDATING", "system")
    problems = validate_intake(template, data)
    if problems:
        req.move("NEEDS_INFO", "system", "; ".join(problems))
        return req
    req.move("DRAFTING", "system")
    results = draft_document(provider, template, data)
    flags = [f"{sid}: {p}" for sid, r in results.items() for p in r.flags]
    req.add_version(data, {sid: r.text for sid, r in results.items()},
                    template.id, template.version, flags, "ai:" + provider.name)
    req.move("LAYOUT_CHECK", "system")
    return req


def render_preview(template: Template, req: Request, out_dir: str) -> tuple[str, LayoutReport]:
    """Render the current version as a DRAFT pdf without touching workflow state."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{req.id}_v{req.current.number}_draft.pdf")
    return path, _render(template, req, path, final=False, reviewer=None)


def layout_check(template: Template, req: Request, out_dir: str) -> LayoutReport:
    """Render a draft preview. If any section is clipped, route to LAYOUT_REVISION."""
    _, report = render_preview(template, req, out_dir)
    if not report.ok:
        req.move("LAYOUT_REVISION", "system", f"clipped: {report.clipped}")
    else:
        detail = "overflow handled: " + ", ".join(report.overflowed) if report.overflowed else "ok"
        req.move("INTERNAL_QA", "system", detail)
    return report


def internal_qa_pass(req: Request, staff: str):
    req.move("REVIEW_PENDING", staff, "frozen for reviewer")


def edit_section(template: Template, req: Request, section_id: str, new_text: str, actor: str):
    """Staff/reviewer edit a single section -> new immutable version (never edits in place)."""
    sections = dict(req.current.sections)
    sections[section_id] = new_text
    if req.state == "SIGNING_PENDING":
        return req.edit_after_approval(sections, actor)
    return req.add_version(req.current.data, sections, template.id, template.version,
                           req.current.flags, actor)


def regenerate(template: Template, req: Request, provider: Provider, section_id: str, actor: str):
    res = regenerate_section(provider, template, section_id, req.current.data)
    return edit_section(template, req, section_id, res.text, actor)


def sign_and_finalize(template: Template, req: Request, out_dir: str, signer: str) -> tuple[str, str]:
    """Only an approved, unchanged version can be signed. Returns (pdf_path, sha256)."""
    req.assert_signable()
    path = os.path.join(out_dir, f"{req.id}_v{req.current.number}_FINAL.pdf")
    report = _render(template, req, path, final=True, reviewer=req.current.approved_by or signer)
    if not report.ok:
        raise WorkflowError(f"layout failed: {report.clipped}")
    req.move("SIGNED", signer, "signature and seal applied")
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    req.move("FINALIZED", "system", f"pdf sha256={digest[:16]}")
    return path, digest


def deliver(req: Request, actor: str = "system"):
    req.move("DELIVERED", actor, "client notified")
