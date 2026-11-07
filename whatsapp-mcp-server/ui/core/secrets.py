"""Secrets stay out of the screen: mask them on the way out, merge them back on the way in.

A secret leaves as {"set": bool} and comes back as: a new text (replace), nothing or {"set": ...} (keep what is
stored) or CLEAR (remove). Records are matched to the stored ones by their id_key, so moving or removing a
record cannot give its secret to another.
"""
import copy
from typing import Any, Dict, Optional

from ui.core.registry import Field, Section

CLEAR = {"clear": True}


def _is_set(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _mask_field(f: Field, value: Any) -> Any:
    if f.secret:
        return {"set": _is_set(value)}
    if f.kind == "records":
        return [_mask_record(f, record) for record in (value or []) if isinstance(record, dict)]
    return copy.deepcopy(value)


def _mask_record(parent: Field, record: Dict[str, Any]) -> Dict[str, Any]:
    return {sub.key: _mask_field(sub, record.get(sub.key)) for sub in parent.fields if sub.key in record or sub.secret}


def mask(section: Section, values: Dict[str, Any]) -> Dict[str, Any]:
    """What the screen may hold: the declared fields only, every secret as {"set": bool}."""
    return {f.key: _mask_field(f, values.get(f.key)) for f in section.fields if f.key in values or f.secret}


def _merged_secret(f: Field, old: Any, incoming: Any, where: str) -> Optional[Any]:
    """The secret to store: a text, or None for none."""
    if incoming is None or incoming == "" or (isinstance(incoming, dict) and set(incoming) == {"set"}):
        return old if _is_set(old) else None
    if incoming == CLEAR:
        return None
    if not isinstance(incoming, str):
        raise ValueError(f"{where}: a secret must be a text")
    return incoming


def _merge_records(f: Field, old_records: Any, incoming: Any) -> list:
    by_id = {}
    if f.id_key:
        for record in old_records or []:
            if isinstance(record, dict) and record.get(f.id_key) is not None:
                by_id[record[f.id_key]] = record
    merged = []
    for record in incoming or []:
        if not isinstance(record, dict):
            raise ValueError(f"{f.key}: each item must be an object")
        old = by_id.get(record.get(f.id_key)) if f.id_key else None
        out = dict(old) if old else {}
        for sub in f.fields:
            if sub.key not in record:
                continue
            _merge_field(out, old or {}, sub, record[sub.key], f"{f.key}.{sub.key}")
        merged.append(out)
    return merged


def _merge_field(out: Dict[str, Any], old: Dict[str, Any], f: Field, incoming: Any, where: str) -> None:
    if f.secret:
        secret = _merged_secret(f, old.get(f.key), incoming, where)
        if secret is None:
            out.pop(f.key, None)
        else:
            out[f.key] = secret
    elif f.kind == "records":
        out[f.key] = _merge_records(f, old.get(f.key), incoming)
    elif incoming is None:
        out.pop(f.key, None)
    else:
        out[f.key] = copy.deepcopy(incoming)


def merge(section: Section, old: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """The values to store: old, with the declared fields that the screen sent changed. What the section does
    not declare stays as it was. A field sent as None is removed (back to its default)."""
    out = copy.deepcopy(old)
    for f in section.fields:
        if f.key in incoming:
            _merge_field(out, old, f, incoming[f.key], f.key)
    return out
