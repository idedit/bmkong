import sys
from pathlib import Path

# Project root in a checkout, or the unpack directory inside the PyInstaller exe.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
