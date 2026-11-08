"""Workflow state machine, immutable versions, approval invalidation and append-only audit log.

Framework-independent: the Django models in web/ mirror these structures.
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

# state -> allowed next states (taken from the proposed workflow diagram)
TRANSITIONS: dict[str, set[str]] = {
    "SUBMITTED": {"VALIDATING"},
    "VALIDATING": {"NEEDS_INFO", "DRAFTING"},
    "NEEDS_INFO": {"VALIDATING"},
    "DRAFTING": {"LAYOUT_CHECK"},
    "LAYOUT_CHECK": {"LAYOUT_REVISION", "INTERNAL_QA"},
    "LAYOUT_REVISION": {"LAYOUT_CHECK"},
    "INTERNAL_QA": {"REVIEW_PENDING"},
    "REVIEW_PENDING": {"REVISION", "REJECTED", "SIGNING_PENDING"},
    "REVISION": {"INTERNAL_QA"},
    "SIGNING_PENDING": {"SIGNED", "REVIEW_PENDING"},   # back to review if content changed
    "SIGNED": {"FINALIZED"},
    "FINALIZED": {"DELIVERED"},
    "REJECTED": set(),
    "DELIVERED": set(),
}
LOCKED_STATES = {"SIGNING_PENDING", "SIGNED", "FINALIZED", "DELIVERED", "REVIEW_PENDING"}


class WorkflowError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(data: dict, sections: dict[str, str], template_id: str, template_version: int) -> str:
    """Hash of everything that appears in the document. Approval is bound to this value."""
    blob = json.dumps({"data": data, "sections": sections, "t": [template_id, template_version]},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class Version:
    number: int
    data: dict
    sections: dict[str, str]
    template_id: str
    template_version: int
    hash: str = ""
    flags: list[str] = field(default_factory=list)
    approved_hash: str | None = None           # set on approval; must equal hash to sign
    approved_by: str | None = None

    def __post_init__(self):
        self.hash = content_hash(self.data, self.sections, self.template_id, self.template_version)


@dataclass
class Request:
    id: str
    state: str = "SUBMITTED"
    versions: list[Version] = field(default_factory=list)
    audit: list[dict] = field(default_factory=list)      # append-only

    # ---- (de)serialisation for storage (used by the Django layer) --------
    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Request":
        return cls(id=d["id"], state=d["state"],
                   versions=[Version(**v) for v in d["versions"]], audit=list(d["audit"]))

    # ---- audit ----------------------------------------------------------
    def log(self, actor: str, action: str, detail: str = ""):
        prev = self.audit[-1]["entry_hash"] if self.audit else ""
        entry = {"ts": _now(), "actor": actor, "action": action, "detail": detail,
                 "state": self.state, "prev": prev}
        entry["entry_hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
        self.audit.append(entry)                         # hash-chained: tampering is detectable

    def verify_audit(self) -> bool:
        prev = ""
        for e in self.audit:
            body = {k: v for k, v in e.items() if k != "entry_hash"}
            if body["prev"] != prev or hashlib.sha256(
                    json.dumps(body, sort_keys=True).encode()).hexdigest() != e["entry_hash"]:
                return False
            prev = e["entry_hash"]
        return True

    # ---- transitions ----------------------------------------------------
    def move(self, new_state: str, actor: str, detail: str = ""):
        if new_state not in TRANSITIONS.get(self.state, set()):
            raise WorkflowError(f"illegal transition {self.state} -> {new_state}")
        self.state = new_state
        self.log(actor, f"state:{new_state}", detail)

    @property
    def current(self) -> Version:
        return self.versions[-1]

    def add_version(self, data, sections, template_id, template_version, flags, actor) -> Version:
        if self.state in {"SIGNED", "FINALIZED", "DELIVERED"}:
            raise WorkflowError("signed documents are immutable; create a new request")
        v = Version(len(self.versions) + 1, data, sections, template_id, template_version, flags=flags)
        self.versions.append(v)
        self.log(actor, "version_created", f"v{v.number} hash={v.hash[:12]}")
        return v

    # ---- review ---------------------------------------------------------
    def approve(self, reviewer: str):
        if self.state != "REVIEW_PENDING":
            raise WorkflowError("only a version pending review can be approved")
        v = self.current
        v.approved_hash, v.approved_by = v.hash, reviewer
        self.move("SIGNING_PENDING", reviewer, f"approved v{v.number} hash={v.hash[:12]}")

    def request_changes(self, reviewer: str, comment: str):
        if self.state != "REVIEW_PENDING":
            raise WorkflowError("no version pending review")
        self.move("REVISION", reviewer, comment)

    def reject(self, reviewer: str, comment: str):
        self.move("REJECTED", reviewer, comment)

    def edit_after_approval(self, new_sections: dict[str, str], actor: str) -> Version:
        """Any content change after approval invalidates it: new version, back to review."""
        if self.state != "SIGNING_PENDING":
            raise WorkflowError("edit_after_approval only applies while awaiting signature")
        old = self.current
        v = self.add_version(old.data, new_sections, old.template_id, old.template_version,
                             old.flags, actor)
        self.log(actor, "approval_invalidated", f"v{old.number} approval void; v{v.number} needs review")
        self.state = "REVIEW_PENDING"
        return v

    def assert_signable(self):
        v = self.current
        if self.state != "SIGNING_PENDING":
            raise WorkflowError("document is not awaiting signature")
        if not v.approved_hash or v.approved_hash != v.hash:
            raise WorkflowError("current content does not match the approved version")
