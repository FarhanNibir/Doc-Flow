from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_POST

from core import pipeline as P
from core.ai import get_provider
from core.workflow import Request, WorkflowError

from .models import DocRequest, DocTemplate

# No login: the header "Acting as" switcher stores a demo role in the session.
ROLES = {"client": "Client", "staff": "Staff", "reviewer": "Mahmud Karim"}


# ---- helpers -----------------------------------------------------------------
def me(request):
    role = request.session.get("role", "client")
    if role not in ROLES:
        role = "client"
    return SimpleNamespace(role=role, username=role, name=ROLES[role],
                           staffer=(role == "staff"), reviewer=(role == "reviewer"))


def pdf_dir(ref):
    d = settings.PDF_ROOT / ref
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def load(dr):
    return dr.template.core(), Request.from_dict(dr.payload)


def save(dr, req):
    dr.state, dr.payload = req.state, req.to_dict()
    dr.save()


def can_view(u, dr):
    return u.staffer or u.reviewer or dr.created_by == u.username


# ---- views -------------------------------------------------------------------
@require_POST
def set_role(request):
    role = request.POST.get("role", "client")
    request.session["role"] = role if role in ROLES else "client"
    return redirect(request.POST.get("next") or "index")


def index(request):
    u = me(request)
    rows = DocRequest.objects.all()
    if not (u.staffer or u.reviewer):
        rows = rows.filter(created_by=u.username)
    templates = DocTemplate.objects.filter(published=True)
    return render(request, "docs/list.html", {"rows": rows, "templates": templates})


def new_request(request, key):
    u = me(request)
    tpl = DocTemplate.objects.filter(key=key, published=True).order_by("-version").first()
    if not tpl:
        raise Http404
    t = tpl.core()
    data, problems = {}, []
    if request.method == "POST":
        data = {f.name: request.POST.get(f.name, "").strip() for f in t.fields}
        problems = P.validate_intake(t, data)           # missing data never reaches the AI
        if not problems:
            ref = "REQ-" + get_random_string(8).upper()
            req = P.start_request(t, ref, data, get_provider(settings.DOCFLOW_PROVIDER),
                                  actor=u.name)
            P.layout_check(t, req, pdf_dir(ref))
            dr = DocRequest.objects.create(ref=ref, template=tpl, state=req.state,
                                           payload=req.to_dict(), created_by=u.username)
            messages.success(request, f"Draft generated ({dr.ref}).")
            return redirect("detail", ref=dr.ref)
    return render(request, "docs/new.html", {"t": t, "tpl": tpl, "data": data, "problems": problems})


def detail(request, ref):
    u = me(request)
    dr = get_object_or_404(DocRequest, ref=ref)
    if not can_view(u, dr):
        return HttpResponseForbidden("Switch 'Acting as' to Staff or Reviewer to view this request.")
    t, req = load(dr)
    v = req.current if req.versions else None
    secs = [(s, v.sections.get(s.id, "")) for s in t.sections] if v else []
    ctx = {"dr": dr, "req": req, "v": v, "secs": secs, "audit": list(reversed(req.audit)),
           "audit_ok": req.verify_audit(),
           "staffer": u.staffer, "reviewer": u.reviewer,
           "is_approver": v is not None and v.approved_by == u.name}
    return render(request, "docs/detail.html", ctx)


def pdf(request, ref):
    u = me(request)
    dr = get_object_or_404(DocRequest, ref=ref)
    if not can_view(u, dr):
        return HttpResponseForbidden()
    t, req = load(dr)
    if dr.state in ("FINALIZED", "DELIVERED") and dr.final_pdf:
        return FileResponse(open(dr.final_pdf, "rb"), content_type="application/pdf")
    path, _ = P.render_preview(t, req, pdf_dir(ref))
    return FileResponse(open(path, "rb"), content_type="application/pdf")


@require_POST
def action(request, ref, name):
    u = me(request)
    dr = get_object_or_404(DocRequest, ref=ref)
    if not can_view(u, dr):
        return HttpResponseForbidden()
    t, req = load(dr)
    who = u.name
    try:
        if name == "qa" and u.staffer:
            P.internal_qa_pass(req, who)
        elif name == "edit" and u.staffer:
            sid, text = request.POST["section_id"], request.POST["text"].strip()
            was_revision = req.state == "REVISION"
            P.edit_section(t, req, sid, text, who)
            if was_revision:
                req.move("INTERNAL_QA", who, "revised")
            _, rep = P.render_preview(t, req, pdf_dir(ref))
            if not rep.ok:
                messages.warning(request, f"Layout problem, content clipped in: {rep.clipped}")
            if req.state == "REVIEW_PENDING":
                messages.warning(request, "Edit after approval: approval was invalidated.")
        elif name == "regen" and u.staffer:
            P.regenerate(t, req, get_provider(settings.DOCFLOW_PROVIDER), request.POST["section_id"], who)
            if req.state == "REVISION":
                req.move("INTERNAL_QA", who, "revised")
        elif name in ("approve", "changes", "reject") and u.reviewer:
            if dr.created_by == u.username:
                return HttpResponseForbidden("Independent review required: you created this request.")
            comment = request.POST.get("comment", "").strip()
            if name == "approve":
                req.approve(who)
            elif name == "changes":
                req.request_changes(who, comment or "Changes requested")
            else:
                req.reject(who, comment or "Rejected")
        elif name == "sign" and u.reviewer:
            if req.current.approved_by != who:
                return HttpResponseForbidden("Only the approving reviewer can sign.")
            path, digest = P.sign_and_finalize(t, req, pdf_dir(ref), who)
            dr.final_pdf, dr.final_sha256 = path, digest
        elif name == "deliver" and u.staffer:
            P.deliver(req, who)
        else:
            return HttpResponseForbidden()
    except (WorkflowError, KeyError) as e:
        messages.error(request, str(e))
        return redirect("detail", ref=ref)
    save(dr, req)
    return redirect("detail", ref=ref)