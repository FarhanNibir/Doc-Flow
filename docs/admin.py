import json

from django import forms
from django.contrib import admin

from .models import DocRequest, DocTemplate


class TemplateForm(forms.ModelForm):
    definition = forms.CharField(widget=forms.Textarea(attrs={"rows": 36, "cols": 110,
                                                              "style": "font-family:monospace"}))

    class Meta:
        model = DocTemplate
        fields = "__all__"

    def clean_definition(self):
        try:
            return json.loads(self.cleaned_data["definition"])
        except json.JSONDecodeError as e:
            raise forms.ValidationError(f"Invalid JSON: {e}")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance and self.instance.pk:
            self.initial["definition"] = json.dumps(self.instance.definition, indent=2, ensure_ascii=False)


@admin.register(DocTemplate)
class DocTemplateAdmin(admin.ModelAdmin):
    form = TemplateForm
    list_display = ("key", "version", "published", "updated")
    actions = ["new_version"]

    @admin.action(description="Create a new draft version from selected template")
    def new_version(self, request, queryset):
        for t in queryset:
            d = dict(t.definition, version=t.version + 1)
            DocTemplate.objects.create(key=t.key, version=t.version + 1, definition=d, published=False)


@admin.register(DocRequest)
class DocRequestAdmin(admin.ModelAdmin):
    list_display = ("ref", "state", "template", "created_by", "created")
    readonly_fields = ("payload", "final_sha256", "final_pdf")
