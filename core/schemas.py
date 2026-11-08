"""Pydantic models: the template schema (admin-editable JSON) and AI output schema."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


class PageSpec(BaseModel):
    size: Literal["A4", "LETTER"] = "A4"
    margin_mm: float = 15


class Zone(BaseModel):
    """A rectangular print region. Units are millimetres, origin top-left of the page."""
    id: str
    x: float
    y: float
    w: float
    h: float
    kind: Literal["header", "text", "signature", "seal", "footer"] = "text"


class SectionSpec(BaseModel):
    id: str
    title: str
    zone: str                                  # zone id where text starts
    overflow_zone: Optional[str] = None        # zone to continue into (next page uses same zone)
    input_fields: list[str]                    # ONLY these fields are sent to the AI
    instruction: str                           # per-section AI rule (admin editable)
    max_words: int = 80
    mode: Literal["ai", "copy"] = "ai"         # "copy" = verified identifiers, never AI-written


class FieldSpec(BaseModel):
    name: str
    label: str
    type: Literal["text", "date", "textarea"] = "text"
    required: bool = True


class Template(BaseModel):
    id: str
    version: int
    name: str
    page: PageSpec = PageSpec()
    font_name: str = "Helvetica"
    font_size: float = 10
    line_height: float = 1.45
    fields: list[FieldSpec]
    zones: list[Zone]
    sections: list[SectionSpec]

    @model_validator(mode="after")
    def _check_refs(self):
        zone_ids = {z.id for z in self.zones}
        field_names = {f.name for f in self.fields}
        for s in self.sections:
            if s.zone not in zone_ids:
                raise ValueError(f"section {s.id}: unknown zone {s.zone}")
            if s.overflow_zone and s.overflow_zone not in zone_ids:
                raise ValueError(f"section {s.id}: unknown overflow zone")
            missing = set(s.input_fields) - field_names
            if missing:
                raise ValueError(f"section {s.id}: unknown input fields {missing}")
        return self


class SectionOutput(BaseModel):
    """What the AI must return for each section (validated, never trusted blindly)."""
    text: str = Field(min_length=1)
    missing_fields: list[str] = []
    source_fields: list[str] = []              # which input fields the text relied on
