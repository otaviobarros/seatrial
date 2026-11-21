"""Host adapter registry.

Adapters come from three places, in this order:
  1. built-in modules in this package that define `ADAPTER`
  2. modules named in `[hosts] modules` of seatrial.toml
  3. installed distributions that declare the `seatrial.hosts` entry point group
A later source cannot replace an adapter name that an earlier source registered.
"""
import importlib
from importlib import metadata
import pkgutil

from seatrial import config
from seatrial.ports import HostAdapter

ENTRY_POINTS = 'seatrial.hosts'


def _adapter(module, origin):
    adapter = getattr(module, 'ADAPTER', None)
    if adapter is not None and not isinstance(adapter, HostAdapter):
        raise ValueError(f'{origin}: ADAPTER must be a HostAdapter instance')
    return adapter


def _add(adapters, adapter, origin):
    if not adapter.name or not adapter.default_model:
        raise ValueError(f'{origin}: an adapter must set name and default_model')
    if adapter.name in adapters:
        raise ValueError(f'{origin}: host {adapter.name!r} is already registered')
    adapters[adapter.name] = adapter


def available():
    """All registered adapters by name."""
    adapters = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith('_'):
            continue
        adapter = _adapter(importlib.import_module(f'{__name__}.{info.name}'), info.name)
        if adapter is not None:
            if adapter.name != info.name:
                raise ValueError(f'adapter {adapter.name!r} must live in hosts/{adapter.name}.py')
            _add(adapters, adapter, info.name)
    active = config.active()
    for name in (active.host_modules if active else []):
        adapter = _adapter(importlib.import_module(name), name)
        if adapter is None:
            raise ValueError(f'{name}: the module defines no ADAPTER')
        _add(adapters, adapter, name)
    for point in metadata.entry_points(group=ENTRY_POINTS):
        loaded = point.load()
        adapter = loaded if isinstance(loaded, HostAdapter) else _adapter(loaded, point.value)
        if adapter is not None and adapter.name not in adapters:
            _add(adapters, adapter, point.value)
    return adapters


def get(name):
    adapters = available()
    if name not in adapters:
        raise ValueError(f'unknown host {name!r}; registered: {", ".join(sorted(adapters))}')
    return adapters[name]


def model(name):
    """The configured model for a host, else the adapter default."""
    active = config.active()
    return (active.models.get(name) if active else None) or get(name).default_model
