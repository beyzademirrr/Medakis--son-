"""MedAkis Streamlit giriş noktası.

Çalıştırma (proje kökünde):
    .\\.venv\\Scripts\\python.exe -m streamlit run app.py              (Normal: veritabanlı Servis paneli)
    .\\.venv\\Scripts\\python.exe -m streamlit run app.py -- --demo    (Sentetik Demo: veritabanısız)

Demo modu ayrıca `?mod=demo` URL parametresi, `MEDAKIS_MODE=demo` ortam değişkeni ya da kenar çubuğundan seçilebilir.
"""

import sys
from pathlib import Path

# src/ düzeni: paket kurulumu gerektirmeden medakis'i içe aktarabilmek için.
SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medakis.ui.home import main  # noqa: E402

main()
