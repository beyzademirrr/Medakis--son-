r"""MedAkis Komuta Merkezi giriş noktası (Sentetik Demo).

    .\.venv\Scripts\python.exe -m streamlit run dashboard.py
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medakis.ui.command_center import main  # noqa: E402

main()
