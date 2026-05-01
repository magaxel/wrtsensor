"""Stub homeassistant and asyncssh so coordinator's pure functions are importable without HA."""

import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_WRT = _ROOT / "custom_components" / "wrtsensor"


def _stub(path: str) -> types.ModuleType:
    if path in sys.modules:
        return sys.modules[path]
    mod = types.ModuleType(path.split(".")[-1])
    mod.__path__ = []  # type: ignore[assignment]
    sys.modules[path] = mod
    return mod


def _load(name: str, path: Path, package: str | None = None) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    if package:
        mod.__package__ = package
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ── asyncssh stub ──────────────────────────────────────────────────────────────
_asyncssh = _stub("asyncssh")
_asyncssh.connect = None  # type: ignore[attr-defined]
_asyncssh.Error = Exception  # type: ignore[attr-defined]
_asyncssh.PermissionDenied = Exception  # type: ignore[attr-defined]

# ── voluptuous stub ────────────────────────────────────────────────────────────
_vol = _stub("voluptuous")
_vol.Required = lambda key: key  # type: ignore[attr-defined]

# ── homeassistant stubs ────────────────────────────────────────────────────────
for _p in [
    "homeassistant",
    "homeassistant.components",
    "homeassistant.components.http",
    "homeassistant.components.websocket_api",
    "homeassistant.config_entries",
    "homeassistant.core",
    "homeassistant.helpers",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.entity_registry",
]:
    _stub(_p)

# Bind child modules as attributes on parents so `from x import y` works.
sys.modules["homeassistant"].helpers = sys.modules["homeassistant.helpers"]  # type: ignore[attr-defined]
sys.modules["homeassistant"].components = sys.modules["homeassistant.components"]  # type: ignore[attr-defined]
sys.modules["homeassistant"].config_entries = sys.modules[
    "homeassistant.config_entries"
]  # type: ignore[attr-defined]
sys.modules["homeassistant"].core = sys.modules["homeassistant.core"]  # type: ignore[attr-defined]
sys.modules["homeassistant.helpers"].update_coordinator = sys.modules[
    "homeassistant.helpers.update_coordinator"
]  # type: ignore[attr-defined]
sys.modules["homeassistant.helpers"].entity_registry = sys.modules[
    "homeassistant.helpers.entity_registry"
]  # type: ignore[attr-defined]
sys.modules["homeassistant.components"].http = sys.modules[
    "homeassistant.components.http"
]  # type: ignore[attr-defined]
sys.modules["homeassistant.components"].websocket_api = sys.modules[
    "homeassistant.components.websocket_api"
]  # type: ignore[attr-defined]

# Minimal entity_registry stub — test_init.py replaces these with a functional
# in-memory registry; default stubs keep other tests' __init__ imports happy.
_er_default = sys.modules["homeassistant.helpers.entity_registry"]
if not hasattr(_er_default, "async_get"):
    _er_default.async_get = lambda hass: None  # type: ignore[attr-defined]
if not hasattr(_er_default, "async_entries_for_config_entry"):
    _er_default.async_entries_for_config_entry = lambda reg, eid: []  # type: ignore[attr-defined]

sys.modules["homeassistant.components.http"].StaticPathConfig = object  # type: ignore[attr-defined]
_ws = sys.modules["homeassistant.components.websocket_api"]
_ws.ActiveConnection = object  # type: ignore[attr-defined]
_ws.async_register_command = lambda hass, handler: None  # type: ignore[attr-defined]
_ws.async_response = lambda fn: fn  # type: ignore[attr-defined]
_ws.websocket_command = lambda schema: lambda fn: fn  # type: ignore[attr-defined]
sys.modules["homeassistant.config_entries"].ConfigEntry = object  # type: ignore[attr-defined]
sys.modules["homeassistant.core"].HomeAssistant = object  # type: ignore[attr-defined]

_uc = sys.modules["homeassistant.helpers.update_coordinator"]


class _DataUpdateCoordinator:
    def __init__(self, *a, **kw):
        self.data = None

    def __class_getitem__(cls, item):
        return cls

    def async_set_updated_data(self, data):  # pragma: no cover - test stub
        self.data = data

    def async_update_listeners(self):  # pragma: no cover - test stub
        return None

    async def async_shutdown(self):  # pragma: no cover - test stub
        return None


class _UpdateFailed(Exception):
    pass


_uc.DataUpdateCoordinator = _DataUpdateCoordinator  # type: ignore[attr-defined]
_uc.UpdateFailed = _UpdateFailed  # type: ignore[attr-defined]

# ── set up custom_components.wrtsensor package hierarchy ─────────────────────
_cc = types.ModuleType("custom_components")
_cc.__path__ = [str(_ROOT / "custom_components")]  # type: ignore[assignment]
sys.modules["custom_components"] = _cc

_pkg = types.ModuleType("custom_components.wrtsensor")
_pkg.__path__ = [str(_WRT)]  # type: ignore[assignment]
_pkg.__package__ = "custom_components.wrtsensor"
sys.modules["custom_components.wrtsensor"] = _pkg

# load const first (no relative imports of its own)
_load(
    "custom_components.wrtsensor.const",
    _WRT / "const.py",
    "custom_components.wrtsensor",
)

# load parser (depends only on stdlib)
_load(
    "custom_components.wrtsensor.parser",
    _WRT / "parser.py",
    "custom_components.wrtsensor",
)

# load coordinator with package context so relative imports resolve
_coord = _load(
    "custom_components.wrtsensor.coordinator",
    _WRT / "coordinator.py",
    "custom_components.wrtsensor",
)
