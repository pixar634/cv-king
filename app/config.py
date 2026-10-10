from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
DATA = ROOT / "data"
KNOWLEDGE = ROOT / "knowledge"
OUTPUT = ROOT / "output"
STATIC = APP / "static"
FONTS = STATIC / "fonts"
TEMPLATES = APP / "templates"

BASE_CV = DATA / "base_cv.json"
ORIGINAL_TXT = DATA / "original_cv.txt"
TRACKER = OUTPUT / "applications.csv"

PORT = 8765
