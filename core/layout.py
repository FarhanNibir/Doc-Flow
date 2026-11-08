"""Deterministic zone-based PDF renderer with overflow detection.

Coordinates in the template are millimetres from the TOP-LEFT of the page; ReportLab uses
points from the BOTTOM-LEFT, so we convert in one place (`_rect`).
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .schemas import Template, Zone

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")


@dataclass
class LayoutReport:
    pages: int = 1
    overflowed: list[str] = field(default_factory=list)       # section ids that spilled to a new page
    clipped: list[str] = field(default_factory=list)          # sections that could not fit at all
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.clipped


def register_font(template: Template) -> str:
    """Use a TTF from fonts/ if the template names one (needed for Bangla); else Helvetica."""
    name = template.font_name
    if name == "Helvetica":
        return name
    path = os.path.join(FONT_DIR, f"{name}.ttf")
    if os.path.exists(path):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, path))
        return name
    return "Helvetica"          # graceful fallback; caller sees a warning


def wrap_text(text: str, font: str, size: float, width_pt: float) -> list[str]:
    """Greedy word wrap using real font metrics (measure BEFORE drawing)."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if pdfmetrics.stringWidth(trial, font, size) <= width_pt:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


class Renderer:
    def __init__(self, template: Template, path: str):
        self.t = template
        self.page_w, self.page_h = A4 if template.page.size == "A4" else LETTER
        self.c = canvas.Canvas(path, pagesize=(self.page_w, self.page_h))
        self.font = register_font(template)
        self.report = LayoutReport()
        self.zones = {z.id: z for z in template.zones}
        if self.font != template.font_name:
            self.report.warnings.append(
                f"font '{template.font_name}' not found in fonts/, fell back to {self.font}")

    # ---- geometry -------------------------------------------------------
    def _rect(self, z: Zone):
        """(x, y_bottom, w, h) in points, ReportLab coordinates."""
        return z.x * mm, self.page_h - (z.y + z.h) * mm, z.w * mm, z.h * mm

    def draw_zone_outline(self, z: Zone):
        x, y, w, h = self._rect(z)
        self.c.saveState()
        self.c.setStrokeColorRGB(0.6, 0.72, 0.9)
        self.c.setDash(3, 3)
        self.c.rect(x, y, w, h)
        self.c.restoreState()

    # ---- text -----------------------------------------------------------
    def draw_block(self, z: Zone, title: str, body: str, cursor_mm: float, section_id: str,
                   overflow_zone: Zone | None):
        """Draw title+body in zone starting at cursor_mm (mm from zone top).
        Returns the new cursor. Continues in overflow zone / next page when it does not fit."""
        size = self.t.font_size
        lead = size * self.t.line_height
        x, y_bottom, w, h = self._rect(z)
        lines = wrap_text(body, self.font, size, w)
        title_h = lead * 1.3
        # does at least the title + first line fit?
        cur = cursor_mm * mm
        if cur + title_h + lead > h:
            cur = self._new_page(z)
            self._note_overflow(section_id)
        self.c.setFont("Helvetica-Bold" if self.font == "Helvetica" else self.font, size + 1)
        self.c.drawString(x, y_bottom + h - cur - size, title)
        cur += title_h
        self.c.setFont(self.font, size)
        for line in lines:
            if cur + lead > h:
                cur = self._new_page(z)
                self._note_overflow(section_id)
                if cur + lead > h:               # zone too small even for one line on a fresh page
                    if section_id not in self.report.clipped:
                        self.report.clipped.append(section_id)
                    return cur / mm
            self.c.drawString(x, y_bottom + h - cur - size, line)
            cur += lead
        return cur / mm + 2.5                    # small gap after the block

    def _note_overflow(self, section_id: str):
        if section_id not in self.report.overflowed:
            self.report.overflowed.append(section_id)
            self.report.warnings.append(
                f"section '{section_id}' overflowed its zone and continues on page {self.report.pages}")

    def _new_page(self, z: Zone) -> float:
        self.c.showPage()
        self.report.pages += 1
        return 0.0

    # ---- public ---------------------------------------------------------
    def render(self, values: dict[str, str], sections: dict[str, str], *, header: dict,
               status_line: str, guides: bool = False, watermark: str | None = None):
        for z in self.t.zones:
            if guides:
                self.draw_zone_outline(z)
        self._draw_header(header)
        cursors: dict[str, float] = {}
        for spec in self.t.sections:
            z = self.zones[spec.zone]
            body = sections.get(spec.id, "")
            cursors[z.id] = self.draw_block(z, spec.title, body, cursors.get(z.id, 0.0),
                                            spec.id, self.zones.get(spec.overflow_zone or ""))
        self._draw_footer(status_line)
        if watermark:
            self._watermark(watermark)
        return self

    def _draw_header(self, header: dict):
        z = next((z for z in self.t.zones if z.kind == "header"), None)
        if not z:
            return
        x, y, w, h = self._rect(z)
        self.c.setFont("Helvetica-Bold", 15)
        self.c.drawString(x, y + h - 16, header.get("title", ""))
        self.c.setFont("Helvetica", 8.5)
        self.c.drawString(x, y + h - 30, header.get("subtitle", ""))
        self.c.line(x, y + 2, x + w, y + 2)

    def _draw_footer(self, status_line: str):
        z = next((z for z in self.t.zones if z.kind == "footer"), None)
        if not z:
            return
        x, y, w, h = self._rect(z)
        self.c.setFont("Helvetica", 8)
        self.c.drawString(x, y + 4, status_line)

    def _watermark(self, text: str):
        self.c.saveState()
        self.c.setFont("Helvetica-Bold", 60)
        self.c.setFillColorRGB(0.85, 0.88, 0.93)
        self.c.translate(self.page_w / 2, self.page_h / 2)
        self.c.rotate(35)
        self.c.drawCentredString(0, 0, text)
        self.c.restoreState()

    def stamp(self, zone_id: str, label_lines: list[str], kind: str):
        """Draw a DEMO signature/seal inside its reserved zone only (never outside)."""
        z = self.zones[zone_id]
        x, y, w, h = self._rect(z)
        self.c.saveState()
        self.c.setStrokeColorRGB(0.19, 0.36, 0.58)
        self.c.setFillColorRGB(0.19, 0.36, 0.58)
        if kind == "seal":
            r = min(w, h) / 2 - 2
            cx, cy = x + w / 2, y + h / 2
            self.c.setLineWidth(2.5); self.c.circle(cx, cy, r)
            self.c.setLineWidth(0.8); self.c.circle(cx, cy, r - 4)
            self.c.setFont("Helvetica-Bold", 8)
            for i, ln in enumerate(label_lines):
                self.c.drawCentredString(cx, cy + 6 - i * 10, ln)
        else:
            self.c.setFont("Helvetica-Oblique", 20)
            self.c.drawString(x + 4, y + h / 2 - 2, label_lines[0])
            self.c.setLineWidth(0.6)
            self.c.line(x, y + 6, x + w, y + 6)
            self.c.setFont("Helvetica", 7)
            for i, ln in enumerate(label_lines[1:]):
                self.c.drawString(x, y - 4 - i * 8, ln)
        self.c.restoreState()

    def save(self) -> LayoutReport:
        self.c.save()
        return self.report
