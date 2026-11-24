"""Kontrollü corpus belgesini ONAYLA ya da EMEKLİYE AYIR (yalnız proje sahibi, yerel ve etkileşimli).

    .\\.venv\\Scripts\\python.exe scripts\\rag_belge_onay.py --kaynak SYN-PRC-DEVIR --surum 1.0 --karar onayla ^
        --aktor "ad soyad" --gerekce "içerik incelendi"

- Komut yalnız etkileşimli terminalde çalışır ve `ONAYLIYORUM <kaynak>@<surum>` ifadesinin elle yazılmasını ister.
- Onay belgenin güncel içerik özetine bağlanır; içerik sonradan değişirse belge sorguya giremez.
- Olay, RAG deposundaki ekleme-yalnız zincirli izine (`belge_onaylari.jsonl`) yazılır. Veritabanına yazılmaz.
- Bu komutu yazılım geliştirme sürecinde başka biri ya da otomatik bir araç çalıştırmaz.
- Güvenlik fikstürleri (`purpose: guvenlik_fiksturu`) bu komutla onaylanamaz; yalnız testlerde geçici izlerle kullanılır.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.config import load_settings  # noqa: E402
from medakis.rag.errors import RagError  # noqa: E402
from medakis.rag.ingestion.approval_ledger import APPROVE, RETIRE, ApprovalLedger  # noqa: E402
from medakis.rag.ingestion.corpus_loader import load_corpus  # noqa: E402
from medakis.rag.vector_store.location import check_store_dir  # noqa: E402

CORPUS = ROOT / "corpus" / "kontrollu"
LEDGER_NAME = "belge_onaylari.jsonl"


def run(argv: list[str], *, ask: Callable[[str], str] = input, interactive: Callable[[], bool] = lambda: sys.stdin.isatty(),
        corpus_dir: Path = CORPUS, store_dir: Path | None = None, now: Callable[[], datetime] = lambda: datetime.now(UTC),
        out: Callable[[str], None] = print) -> int:  # fmt: skip
    parser = argparse.ArgumentParser(description="Kontrollü corpus belge onayı (yalnız proje sahibi)")
    parser.add_argument("--kaynak", required=True)
    parser.add_argument("--surum", required=True)
    parser.add_argument("--karar", required=True, choices=(APPROVE, RETIRE))
    parser.add_argument("--aktor", required=True)
    parser.add_argument("--gerekce", required=True)
    args = parser.parse_args(argv)
    if not interactive():
        out("REDDEDİLDİ: komut yalnız etkileşimli terminalde çalışır.")
        return 2
    try:
        settings = load_settings(ROOT, env={})
        store = check_store_dir(store_dir or settings.rag_store_dir, min_free_gb=settings.rag_min_free_gb)
        corpus = load_corpus(corpus_dir)
        uid = f"{args.kaynak}@{args.surum}"
        doc = corpus.by_uid().get(uid)
        if doc is None:
            out(f"REDDEDİLDİ: {uid} corpus'ta yüklenemedi. Sorunlar: {[p for p in corpus.problems if p.startswith(uid)]}")
            return 2
        r = doc.record
        if r.purpose != "kaynak":
            kind = "güvenlik fikstürü" if r.purpose == "guvenlik_fiksturu" else "sürüm fikstürü"
            out(f"REDDEDİLDİ: {uid} {kind}dür; normal kaynak onayına girmez (yalnız testlerde kullanılır).")
            return 2
        out(f"Belge:{r.uid} · {r.source_name} · {r.doc_type} · geçerlilik {r.valid_from:%Y-%m-%d} – "
            f"{'açık' if r.valid_until is None else f'{r.valid_until:%Y-%m-%d}'} · içerik özeti {r.content_sha256[:16]}…")  # fmt: skip
        phrase = f"ONAYLIYORUM {uid}"
        try:
            typed = ask(f"Karar '{args.karar}'. Onaylamak için tam olarak yazın: {phrase}\n> ")
        except (EOFError, KeyboardInterrupt):  # girdi yok ya da kullanıcı kesti: onay yok
            out("VAZGEÇİLDİ: ifade girilmedi; iz değişmedi.")
            return 1
        if typed.strip() != phrase:
            out("VAZGEÇİLDİ: ifade eşleşmedi; iz değişmedi.")
            return 1
        event = ApprovalLedger(store / LEDGER_NAME).append(args.karar, r, args.aktor, args.gerekce, now(),
                                                           [d.record for d in corpus.documents])  # fmt: skip
    except RagError as exc:
        out(f"REDDEDİLDİ: {exc.code}")
        return 2
    out(f"Kaydedildi: sıra {event['sira']} · {event['karar']} · özet {event['ozet'][:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
