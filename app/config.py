from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
DATA = ROOT / "data"            # the first profile's data (see profiles.py); others live under profiles/<id>/
KNOWLEDGE = ROOT / "knowledge"
OUTPUT = ROOT / "output"        # the first profile's applications
STATIC = APP / "static"
FONTS = STATIC / "fonts"
TEMPLATES = APP / "templates"

PORT = 8765


def data_dir() -> Path:
    """The current profile's data folder: its CV, strategy, sources, goal and salary."""
    from .profiles import data_dir as d
    return d()


def output_dir() -> Path:
    """The current profile's applications, scans and queue."""
    from .profiles import output_dir as o
    return o()
