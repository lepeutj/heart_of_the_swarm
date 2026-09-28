"""Heart of the Swarm agent runtime."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("heart-of-the-swarm")
except PackageNotFoundError:
    __version__ = "0.0.0"
