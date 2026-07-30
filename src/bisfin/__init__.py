"""Bisfin typed application and PostgreSQL access foundation."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bisfin")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0.0.0"

__all__ = ["__version__"]
