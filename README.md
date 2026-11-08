# DocFlow — AI document automation prototype

A learning/portfolio project: client intake → section-wise AI drafting → fixed-zone PDF layout with
overflow handling → reviewer approval → signature & seal → locked PDF with a tamper-evident audit trail.

> Demo project. Signature and seal are obviously fake ("DEMO SEAL"). Not for legal use.

## Layout
```
core/        framework-independent engine (tested)
  schemas.py   Pydantic: template JSON + AI output schema
  ai.py        provider adapter (mock / OpenAI / Gemini), per-section prompts, validation, retry
  layout.py    ReportLab zone renderer: measure text, wrap, overflow -> next page, never silently shrink
  workflow.py  state machine, immutable versions, approval bound to content hash, hash-chained audit log
  pipeline.py  start_request, layout_check, edit/regenerate, sign_and_finalize, deliver
templates/   admin-editable template JSON (zones, sections, AI instructions, word limits)
web/         Django app on top of core (login, intake form, review console, template admin)
tests/       pytest suite for the engine
demo.py      runs the whole flow from the command line
```

## Quick start (engine only, no Django needed)
```bash
pip install -r requirements.txt
python demo.py                 # offline mock AI; writes PDFs + audit.json to ./out
python demo.py --long          # long findings: shows overflow onto page 2
python -m pytest -q
```
Real AI: `pip install openai` (or `google-genai`), set `OPENAI_API_KEY` / `GEMINI_API_KEY`, then
`python demo.py --provider openai`. (Adjust the model names in `core/ai.py` to ones your account can use.)

## Web app
```bash
cd web
python manage.py makemigrations docs && python manage.py migrate
python manage.py seed_demo            # sample template + users client/staff/reviewer (password demo12345)
python manage.py runserver
```
Log in as `client` → new request. `staff` passes QA / edits sections. `reviewer` approves, then signs.
Edit templates at `/admin/` → Doc templates (published templates are immutable; use the
"new version" action). Set `DOCFLOW_PROVIDER=openai` to use a real model.

**Status of the web layer:** written but not yet run by the author of this scaffold (Django was not
installable in the build environment). Expect small fixes on first run; the engine under `core/` is the
tested part.

## Design rules enforced by the engine (and tests)
- AI only receives the fields mapped to a section; `mode: "copy"` sections (identifiers) never go through AI.
- AI output must be valid JSON; numbers/IDs not present in the source are flagged; one retry, then human flag.
- Content is hashed. Approval stores that hash. Any later edit makes signing impossible until re-approved.
- Signed documents are immutable; every step is in a hash-chained audit log (`verify_audit()`).
- Signature/seal are drawn only inside their reserved zones and only after approval.

## Known limitations / next steps
1. **Bangla text:** ReportLab does not shape complex scripts. Put a Bengali TTF (e.g. Noto Sans Bengali) in `fonts/`,
   set `font_name` in the template, and add `uharfbuzz`-based shaping, or switch the renderer to WeasyPrint.
2. Move signature/seal images to a protected store, never a public static folder; add real e-signing (pyHanko) if required.
3. Normalise `DocRequest.payload` into separate Version/AuditEvent tables; add Celery for slow AI/PDF jobs.
4. DOCX export (python-docx) and OCR/file-upload intake.
5. Django tests, MFA, S3 storage, Docker.
