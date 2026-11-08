import json

from django.core.exceptions import ValidationError
from django.db import models

from core.schemas import Template


class DocTemplate(models.Model):
    """Admin-editable template. Editing the JSON in Django admin changes layout, section
    instructions and field mapping with NO code change. Published versions are immutable:
    edit => save as a new version."""
    key = models.SlugField()
    version = models.PositiveIntegerField(default=1)
    definition = models.JSONField(help_text="Template JSON (zones, sections, AI instructions)")
    published = models.BooleanField(default=False)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("key", "version")
        ordering = ["key", "-version"]

    def __str__(self):
        return f"{self.key} v{self.version}{' (published)' if self.published else ' (draft)'}"

    def clean(self):
        try:
            t = Template.model_validate(self.definition)      # same validation the engine uses
        except Exception as e:
            raise ValidationError(f"Invalid template: {e}")
        if t.id != self.key or t.version != self.version:
            raise ValidationError("JSON 'id'/'version' must match key/version fields")

    def core(self) -> Template:
        return Template.model_validate(self.definition)

    def save(self, *a, **kw):
        if self.pk:
            old = DocTemplate.objects.get(pk=self.pk)
            if old.published and old.definition != self.definition:
                raise ValidationError("Published templates are immutable; create a new version.")
        super().save(*a, **kw)


class DocRequest(models.Model):
    ref = models.CharField(max_length=40, unique=True)
    template = models.ForeignKey(DocTemplate, on_delete=models.PROTECT)   # pinned version
    state = models.CharField(max_length=30, db_index=True)
    payload = models.JSONField()          # core.workflow.Request.to_dict(): versions + hash-chained audit
    created_by = models.CharField(max_length=150)
    created = models.DateTimeField(auto_now_add=True)
    final_pdf = models.CharField(max_length=300, blank=True)
    final_sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.ref} [{self.state}]"
