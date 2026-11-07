"""Loaded by the Python of the end-to-end tests, and only there: the harness puts this folder on PYTHONPATH.

It changes nothing of the product. It does two things the environment cannot do:
- E2E_BRIDGE_URL: the address the interface asks for the bridge's status (the product reads a fixed local address,
  which a test must not touch because a real bridge may be listening there).
- E2E_EXTRA_SECTIONS_DIR: a folder added to the places where `ui.sections` looks for modules. The product's own
  discovery then finds a module dropped there, exactly as it finds one in the package.
"""
import os

_bridge = os.environ.get("E2E_BRIDGE_URL")
_extra = os.environ.get("E2E_EXTRA_SECTIONS_DIR")

if _bridge:
    import ui.security.bridge as _bridge_module

    _bridge_module.DEFAULT_BASE_URL = _bridge

if _extra:
    import ui.sections as _sections

    _sections.__path__.append(_extra)
