"""Secrets never leave the server: they are shown as {"set": bool} and come back as text, a keep marker or a clear.

Incoming, for a secret field:
  - a non-empty string replaces the stored secret;
  - the key absent, or {"set": <bool>}, keeps the stored secret;
  - {"clear": true} removes it;
  - anything else is refused and the stored secret is kept.
Secrets inside records are matched to the stored record with the same id key, never by position.
"""
import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ui.config.fields import Field, Problem

_KEEP, _CLEAR = "keep", "clear"
_BAD = "must be text, {\"set\": true} to keep it or {\"clear\": true} to remove it"


@dataclass
class Merged:
    values: Dict[str, Any]
    problems: List[Problem] = field(default_factory=list)


def _is_set(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _mask_value(f: Field, value: Any) -> Any:
    if f.kind == "secret":
        return {"set": _is_set(value)}
    if f.kind == "records":
        subs = {sub.key: sub for sub in f.fields}
        return [{k: _mask_value(subs[k], v) for k, v in rec.items() if k in subs} | {k: {"set": False} for k, s in subs.items()
                                                                                       if s.kind == "secret" and k not in rec}
                for rec in (value if isinstance(value, list) else []) if isinstance(rec, dict)]
    return copy.deepcopy(value)


def mask(fields: Sequence[Field], values: Dict[str, Any]) -> Dict[str, Any]:
    """The values of the declared fields with every secret replaced by {"set": bool}; nothing else leaves."""
    out: Dict[str, Any] = {}
    for f in fields:
        if f.kind == "secret":
            out[f.key] = {"set": _is_set(values.get(f.key))}
        elif f.key in values:
            out[f.key] = _mask_value(f, values[f.key])
    return out


def _decide(incoming: Any) -> Optional[str]:
    """keep, clear, or None when the incoming secret is refused. Text is handled by the caller."""
    if isinstance(incoming, dict):
        if set(incoming) == {"set"} and isinstance(incoming["set"], bool):
            return _KEEP
        if set(incoming) == {"clear"} and incoming["clear"] is True:
            return _CLEAR
    return None


def _resolve(path: str, stored: Any, incoming: Any, problems: List[Problem]):
    """(keep_stored, new_text_or_None) for one secret; a refusal is reported and keeps what is stored."""
    if isinstance(incoming, str) and incoming != "":
        return False, incoming
    decision = _decide(incoming)
    if decision == _CLEAR:
        return False, None
    if decision == _KEEP:
        return True, None
    problems.append(Problem(path, _BAD))
    return True, None


def _merge_records(f: Field, stored: Any, incoming: Any, problems: List[Problem]) -> Any:
    if not isinstance(incoming, list):
        return incoming                                  # the field check refuses it
    subs = {sub.key: sub for sub in f.fields}
    by_id: Dict[Any, Dict[str, Any]] = {}
    if f.id_key is not None and isinstance(stored, list):
        for rec in stored:
            if isinstance(rec, dict) and isinstance(rec.get(f.id_key), (str, int)):
                by_id.setdefault(rec[f.id_key], rec)
    merged = []
    for index, rec in enumerate(incoming):
        if not isinstance(rec, dict):
            merged.append(rec)
            continue
        old = by_id.get(rec.get(f.id_key), {}) if f.id_key is not None and isinstance(rec.get(f.id_key), (str, int)) else {}
        out: Dict[str, Any] = {}
        for key, value in rec.items():
            sub = subs.get(key)
            if sub is not None and sub.kind == "secret":
                keep, text = _resolve(f"{f.key}.{index}.{key}", old.get(key), value, problems)
                if keep and _is_set(old.get(key)):
                    out[key] = old[key]
                elif text is not None:
                    out[key] = text
            else:
                out[key] = copy.deepcopy(value)
        for key, sub in subs.items():                     # an absent secret is kept
            if sub.kind == "secret" and key not in rec and _is_set(old.get(key)):
                out[key] = old[key]
        merged.append(out)
    return merged


def merge(fields: Sequence[Field], stored: Dict[str, Any], incoming: Dict[str, Any]) -> Merged:
    """The stored values with `incoming` applied; secrets follow the rules in the module text."""
    values = copy.deepcopy(stored)
    problems: List[Problem] = []
    for f in fields:
        if f.key not in incoming:
            continue
        value = incoming[f.key]
        if f.kind == "secret":
            keep, text = _resolve(f.key, stored.get(f.key), value, problems)
            if keep:
                continue
            if text is None:
                values.pop(f.key, None)
            else:
                values[f.key] = text
        elif f.kind == "records":
            values[f.key] = _merge_records(f, stored.get(f.key), value, problems)
        else:
            values[f.key] = copy.deepcopy(value)
    return Merged(values, problems)
