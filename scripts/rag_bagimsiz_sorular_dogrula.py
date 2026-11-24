"""S4: bağımsız hazırlanan kaynak dışı dev sorularının biçim denetimi (içerik yazdırılmaz).

    .\\.venv\\Scripts\\python.exe scripts\\rag_bagimsiz_sorular_dogrula.py <doldurulmuş_dosya.jsonl>

Denetimler: en az 30 vaka; boş soru yok; soru 10–500 karakter; yinelenen soru yok; mevcut dev sorularıyla
(normalleştirilmiş) birebir aynı soru yok; her vakada yakınlık, gerekçe, hazırlayan ve kontrol eden rolü dolu;
hazırlayan ile kontrol eden farklı; kişisel veri izlenimi veren kalıp (e-posta, telefon, 11 haneli kimlik) yok;
cevaplanamaz olarak işaretli ve gold boş; örnek ya da yer tutucu metin (ÖRNEK, <…>) yok. Çıktıda soru metni YOKTUR; yalnız sayılar ve sorun kodları vardır.
Bu komut dosyayı değiştirmez ve dev kümesine eklemez; ekleme ayrı kararla yapılır.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation" / "asama7"
_PII = re.compile(r"[\w.+-]+@[\w-]+\.\w+|\b\d{11}\b|\b0?5\d{2}[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}\b")
YAKINLIK = {"alana_yakin", "alana_uzak"}


def norm(q: str) -> str:
    return " ".join(re.findall(r"\w+", q.replace("I", "ı").replace("İ", "i").lower()))


def validate(path: Path) -> dict:
    rows = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    existing = {norm(json.loads(x)["question"]) for n in ("dev.jsonl", "dev_ek.jsonl") for x in (EVAL / n).read_text(encoding="utf-8").splitlines()}
    problems: list[str] = []
    seen: set[str] = set()
    for i, r in enumerate(rows):
        key = r.get("case_key", f"#{i}")
        q = r.get("question", "") or ""
        if not (10 <= len(q.strip()) <= 500):
            problems.append(f"{key}:soru_uzunlugu")
        n = norm(q)
        if n in seen:
            problems.append(f"{key}:yinelenen_soru")
        if n and n in existing:
            problems.append(f"{key}:mevcut_dev_sorusuyla_ayni")
        seen.add(n)
        if r.get("answerable") is not False or r.get("gold") not in ([], None) or r.get("split") != "dev":
            problems.append(f"{key}:etiket_kaynak_disi_dev_degil")
        if r.get("yakinlik") not in YAKINLIK:
            problems.append(f"{key}:yakinlik")
        if not (r.get("neden_kaynak_disi") or "").strip():
            problems.append(f"{key}:gerekce_yok")
        h, k = (r.get("hazirlayan_rolu") or "").strip(), (r.get("kontrol_eden_rolu") or "").strip()
        if not h or not k or h == k:
            problems.append(f"{key}:hazirlayan_kontrol_eden")
        if _PII.search(q):
            problems.append(f"{key}:kisisel_veri_kalibi")
        if "ÖRNEK" in q.upper() or "<" in q or ">" in q:
            problems.append(f"{key}:ornek_ya_da_yer_tutucu")  # biçim örneği gerçek veri olarak kabul edilmez
    if len(rows) < 30:
        problems.append(f"vaka_sayisi:{len(rows)}<30")
    return {"vaka": len(rows), "alana_yakin": sum(r.get("yakinlik") == "alana_yakin" for r in rows), "sorun": problems, "gecti": not problems}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Kullanım: rag_bagimsiz_sorular_dogrula.py <dosya.jsonl>")
        sys.exit(2)
    result = validate(Path(sys.argv[1]))
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["gecti"] else 1)
