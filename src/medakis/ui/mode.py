"""Çalışma modu: Normal (veritabanlı, önceki davranış) ya da Sentetik Demo (veritabanısız).

Mod, sekmeler çizilmeden ÖNCE belirlenir; böylece demo modunda ilk çizimde bile veritabanı servisi, repository ya da
bağlantı fabrikası çağrılmaz. Öncelik sırası (ilk bulunan kazanır):
1. Kenar çubuğundaki seçim (oturum içinde açıkça seçilen mod).
2. URL parametresi: `?mod=demo` ya da `?mod=normal`.
3. Ortam değişkeni: `MEDAKIS_MODE=demo` ya da `normal`.
4. Başlatma bayrağı: `streamlit run app.py -- --demo`.
5. Varsayılan: Normal (önceki davranış korunur).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

NORMAL, DEMO = "normal", "demo"
LABELS = {NORMAL: "Normal (veritabanlı)", DEMO: "Sentetik Demo (veritabanısız)"}
_ALIASES = {"demo": DEMO, "sentetik_demo": DEMO, "sentetik": DEMO, "normal": NORMAL}


def _parse(value: object) -> str | None:
    if isinstance(value, (list, tuple)):
        value = value[-1] if value else None
    if not isinstance(value, str):
        return None
    return _ALIASES.get(value.strip().lower())


def resolve_mode(selected: object = None, query: Mapping[str, object] | None = None, env: Mapping[str, str] | None = None,
                 argv: Sequence[str] = ()) -> tuple[str, str]:  # fmt: skip
    """(mod, kaynak). Tanınmayan değerler yok sayılır; sonuç her zaman NORMAL ya da DEMO'dur."""
    for source, value in (("kenar_cubugu", selected), ("url", (query or {}).get("mod")), ("ortam", (env or {}).get("MEDAKIS_MODE"))):
        mode = _parse(value)
        if mode is not None:
            return mode, source
    if "--demo" in argv:
        return DEMO, "baslatma_bayragi"
    return NORMAL, "varsayilan"
