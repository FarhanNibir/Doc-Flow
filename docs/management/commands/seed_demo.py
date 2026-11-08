import json

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from docs.models import DocTemplate


class Command(BaseCommand):
    help = "Create the sample template and three demo users (client, staff, reviewer)."

    def handle(self, *a, **kw):
        path = settings.BASE_DIR / "templates" / "inspection_report.json"
        d = json.loads(path.read_text(encoding="utf-8"))
        DocTemplate.objects.get_or_create(key=d["id"], version=d["version"],
                                          defaults={"definition": d, "published": True})
        rev, _ = Group.objects.get_or_create(name="reviewer")
        for name, staff, is_rev in [("client", False, False), ("staff", True, False),
                                    ("reviewer", False, True)]:
            u, made = User.objects.get_or_create(username=name, defaults={"is_staff": staff})
            if made:
                u.set_password("demo12345"); u.is_staff = staff; u.save()
            if is_rev:
                u.groups.add(rev)
        self.stdout.write("Seeded. Users client/staff/reviewer, password demo12345 (change it!)")
