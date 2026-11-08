"""Section-wise AI drafting with a swappable provider, structured output and validation.

The AI never sees the whole record: each section only receives the fields it is mapped to.
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import ValidationError

from .schemas import SectionOutput, SectionSpec, Template

SYSTEM_RULES = (
    "You write one section of a formal report. Use ONLY the facts in the provided fields. "
    "Never invent readings, names, causes or measurements. If information needed for the "
    "section is absent, list those field names in missing_fields. "
    "Reply with JSON only: {\"text\": str, \"missing_fields\": [str], \"source_fields\": [str]}."
)


class Provider(Protocol):
    name: str
    def complete_json(self, system: str, user: str) -> str: ...


class MockProvider:
    """Deterministic offline provider so the whole pipeline runs and is testable without a key."""
    name = "mock"

    def complete_json(self, system: str, user: str) -> str:
        payload = json.loads(user)
        fields = payload["fields"]
        sec = payload["section"]
        text = " ".join(f"{k.replace('_', ' ').capitalize()}: {v}." .replace("..", ".")
                        for k, v in fields.items() if str(v).strip())
        missing = [k for k, v in fields.items() if not str(v).strip()]
        return json.dumps({"text": text,
                           "missing_fields": missing,
                           "source_fields": [k for k in fields if k not in missing]})


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini"):
        from openai import OpenAI  # imported lazily so the package is optional
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = model

    def complete_json(self, system: str, user: str) -> str:
        r = self.client.chat.completions.create(
            model=self.model, temperature=0.2,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return r.choices[0].message.content


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str = "gemini-2.0-flash"):
        from google import genai
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = model

    def complete_json(self, system: str, user: str) -> str:
        r = self.client.models.generate_content(
            model=self.model, contents=f"{system}\n\n{user}",
            config={"response_mime_type": "application/json", "temperature": 0.2})
        return r.text


def get_provider(name: str | None = None) -> Provider:
    name = (name or os.environ.get("DOCFLOW_PROVIDER", "mock")).lower()
    return {"mock": MockProvider, "openai": OpenAIProvider, "gemini": GeminiProvider}[name]()


@dataclass
class SectionResult:
    section_id: str
    text: str
    missing_fields: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)   # validation problems for the human reviewer
    attempts: int = 1


def _words(s: str) -> int:
    return len(re.findall(r"\S+", s))


def validate_output(out: SectionOutput, spec: SectionSpec, data: dict) -> list[str]:
    """Rule-based checks. Returns a list of problems (empty = passed)."""
    problems = []
    if _words(out.text) > spec.max_words:
        problems.append(f"over word limit ({_words(out.text)} > {spec.max_words})")
    bad_src = set(out.source_fields) - set(spec.input_fields)
    if bad_src:
        problems.append(f"used fields outside its mapping: {sorted(bad_src)}")
    # every number / ID-like token in the text must appear in the source data
    source_blob = " ".join(str(data.get(f, "")) for f in spec.input_fields).lower()
    for tok in re.findall(r"[A-Za-z]*-?\d[\w.-]*", out.text):
        if tok.lower().strip(".") not in source_blob:
            problems.append(f"value '{tok}' not found in source data")
    return problems


def draft_section(provider: Provider, spec: SectionSpec, data: dict) -> SectionResult:
    fields = {f: data.get(f, "") for f in spec.input_fields}

    if spec.mode == "copy":   # verified identifiers are copied exactly, never AI-written
        text = "; ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in fields.items())
        missing = [k for k, v in fields.items() if not str(v).strip()]
        return SectionResult(spec.id, text, missing, ["missing required data"] if missing else [])

    user = json.dumps({"section": {"title": spec.title, "instruction": spec.instruction,
                                   "max_words": spec.max_words}, "fields": fields},
                      ensure_ascii=False)
    problems: list[str] = []
    for attempt in (1, 2):                               # one retry, then flag for a human
        raw = provider.complete_json(SYSTEM_RULES, user)
        try:
            out = SectionOutput.model_validate_json(raw)
        except ValidationError as e:
            problems = [f"invalid JSON structure: {e.errors()[0]['msg']}"]
            continue
        problems = validate_output(out, spec, data)
        if not problems:
            return SectionResult(spec.id, out.text, out.missing_fields, [], attempt)
        user = json.dumps({**json.loads(user), "previous_problems": problems}, ensure_ascii=False)
    text = out.text if "out" in locals() else ""
    return SectionResult(spec.id, text or "[Draft unavailable — manual entry required]",
                         [], problems + ["needs human review"], 2)


def draft_document(provider: Provider, template: Template, data: dict) -> dict[str, SectionResult]:
    return {s.id: draft_section(provider, s, data) for s in template.sections}


def regenerate_section(provider: Provider, template: Template, section_id: str, data: dict) -> SectionResult:
    spec = next(s for s in template.sections if s.id == section_id)
    return draft_section(provider, spec, data)
