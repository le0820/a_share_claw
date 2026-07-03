"""Compatibility shim for running the src-layout package from a checkout."""

from pathlib import Path

_src_pkg = Path(__file__).resolve().parents[1] / "src" / "a_share_claw"
if _src_pkg.exists():
    __path__.insert(0, str(_src_pkg))  # type: ignore[name-defined]

__all__ = ["__version__"]
__version__ = "0.1.0"
