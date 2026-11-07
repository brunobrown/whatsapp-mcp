"""Section forms as JSON: the schema and values of a section, the effect of the client's changes, the outcome of a save.

The form lives in the browser. Each call builds a fresh FormFlow, loads the current file, sets the version the client
loaded and applies the client's changes on top, so a file that changed in between is a conflict and never overwritten.
All the rules (reading raw text, secrets, limits, locks) are in ui.config; this module only translates.
"""
import dataclasses
from typing import Any, Dict, List, Optional, Sequence

from ui.api.services import Services
from ui.config.fields import Field
from ui.config.form import FormFlow, FormView, SaveOutcome

# Pages that are not forms. They sit in the same menu as the sections.
PAGES = (
    {"id": "status", "title": "Status", "icon": "activity", "order": 0, "kind": "page",
     "description": "Whether the bridge is connected, and its counters.", "restart": None},
    {"id": "activity", "title": "Drafts and audit", "icon": "history", "order": 90, "kind": "page",
     "description": "Pending drafts and the latest send and monitor audit rows.", "restart": None},
    {"id": "account", "title": "Account", "icon": "user", "order": 100, "kind": "page",
     "description": "Change the interface password.", "restart": None},
)


def field_schema(f: Field) -> Dict[str, Any]:
    return {
        "key": f.key, "label": f.label, "kind": f.kind, "default": f.default, "help": f.help, "secret": f.secret,
        "env_var": f.env_var, "options": list(f.options), "bounds": list(f.bounds) if f.bounds else None,
        "max_length": f.max_length, "max_items": f.max_items, "multiline": f.multiline,
        "fields": [field_schema(sub) for sub in f.fields], "id_key": f.id_key, "readonly": f.readonly,
        "risk": f.risk, "risk_when": list(f.risk_when),
    }


class FormService:
    def __init__(self, services: Services):
        self._services = services

    def _pending(self, section) -> bool:
        """Whether a value saved in this section waits for a restart to apply: the menu shows it after a reload."""
        waiting = self._services.env.pending().get(section.restart or "", [])
        return any(f.env_var in waiting for f in section.fields if f.env_var)

    def listing(self) -> List[Dict[str, Any]]:
        sections = [{"id": s.id, "title": s.title, "icon": s.icon, "order": s.order, "kind": "section",
                     "description": s.description, "restart": s.restart, "pending": self._pending(s)}
                    for s in self._services.registry.sections()]
        pages = [{**p, "pending": False} for p in PAGES]
        return sorted([*sections, *pages], key=lambda e: (e["order"], e["id"]))

    def has(self, section_id: str) -> bool:
        return any(s.id == section_id for s in self._services.registry.sections())

    def open(self, section_id: str) -> FormFlow:
        """A form flow with the current file loaded. KeyError when the section is not one of the listed ones."""
        if not self.has(section_id):
            raise KeyError(section_id)
        flow = FormFlow(section_id, self._services.registry, self._services.store, self._services.env)
        flow.load()
        return flow

    def apply(self, flow: FormFlow, changes: Dict[str, Any], restore: Sequence[Any] = (),
              version: Optional[str] = None) -> None:
        """The client's changes (raw text, values, secret markers) on top of the file, as of `version`."""
        if version is not None:
            flow.version = version if isinstance(version, str) else ""
        flow.apply(changes, [k for k in restore if isinstance(k, str)])

    def validate(self, flow: FormFlow) -> Dict[str, Any]:
        check = flow.validate()
        return {"errors": check.errors, "general": check.general, "can_save": check.can_save}

    def view(self, flow: FormFlow) -> Dict[str, Any]:
        v: FormView = flow.view()
        s = flow.section
        return {"id": v.section_id, "title": v.title, "description": v.description, "restart": v.restart,
                "status": v.status, "banner": v.banner, "warnings": v.warnings, "version": v.version,
                "read_only": v.read_only, "fields": [field_schema(f) for f in s.fields], "values": v.values,
                "state": v.state}

    def save(self, flow: FormFlow, confirm_replace: bool = False) -> Dict[str, Any]:
        return self.outcome(flow.save(confirm_replace))

    @staticmethod
    def outcome(outcome: SaveOutcome) -> Dict[str, Any]:
        return dataclasses.asdict(outcome)
