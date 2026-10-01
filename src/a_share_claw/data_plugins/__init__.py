"""Portable, lazy data-plugin API used by both CLI and the hosted Agent."""
from .core import DataError, DataRun, Manifest, Provider, Registry, Requirement, preview
from .providers import FRED, NBS, PBC, SEC, TickFlow
from .tdx import EasyTDX


def default_registry() -> Registry:
    registry = Registry()
    for provider in (NBS, PBC, EasyTDX, FRED, SEC, TickFlow):
        registry.register(provider)
    return registry


__all__ = ["DataRun", "DataError", "Manifest", "Provider", "Registry", "Requirement", "default_registry", "preview"]
