"""Loaded by the Python of the end-to-end tests, and only there: the harness puts this folder on PYTHONPATH.

It changes nothing of the product. It does two things the environment cannot do:
- E2E_BRIDGE_URL: the address the interface asks for the bridge's status (the product reads a fixed local address,
  which a test must not touch because a real bridge may be listening there).
- E2E_IDLE_SECONDS: how long a session may sit unused. The product's limit is 30 minutes, too long to wait for.
- E2E_EXTRA_SECTIONS_DIR: a folder added to the places where `ui.sections` looks for modules. The product's own
  discovery then finds a module dropped there, exactly as it finds one in the package.
"""
import os

_bridge = os.environ.get("E2E_BRIDGE_URL")
_extra = os.environ.get("E2E_EXTRA_SECTIONS_DIR")
_idle = os.environ.get("E2E_IDLE_SECONDS")

if _bridge:
    import ui.security.bridge as _bridge_module

    _bridge_module.DEFAULT_BASE_URL = _bridge

if _extra:
    import ui.sections as _sections

    _sections.__path__.append(_extra)

if _idle:
    import ui.api.services as _services

    _build = _services.build_services

    def _build_with_idle(startup_environ=None, idle_seconds=None, announce=None):
        return _build(startup_environ, float(_idle), announce)

    _services.build_services = _build_with_idle
