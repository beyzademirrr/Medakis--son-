"""Sentetik veri komut satırı (yalnız medakis_dev ve medakis_test).

    .\\.venv\\Scripts\\python.exe scripts\\db\\seed.py status --database medakis_test
    .\\.venv\\Scripts\\python.exe scripts\\db\\seed.py apply --database medakis_test
    .\\.venv\\Scripts\\python.exe scripts\\db\\seed.py apply --database medakis_test --sifirla

medakis_dev'e her yazma ve her sıfırlama, veritabanı adının yazılmasını ister (--onay veya
terminalde soru). medakis_dev'de sıfırlamadan önce otomatik yedek alınır. medakis_demo reddedilir.
medakis_dev'de seed rolü kalıcı olarak salt okunurdur; apply, yazma yetkisini owner bağlantısında
yalnız kendi işlemi içinde verir ve commit etmeden geri alır. status "AÇIK" gösterirse bu bir kurulum
hatasıdır (örneğin eski bir yedeğin geri yüklenmesi); seed çalıştırılmamalıdır.

Üretici sürümü: varsayılan 1.0.0. 2.0.0 yalnız medakis_test içindir ve hedef sunucunun sistem
kimliğini ister (status çıktısında "Sunucu sistem kimliği"); şema revizyon 0004'te olmalıdır:

    .\\.venv\\Scripts\\python.exe scripts\\db\\seed.py apply --database medakis_test --uretici 2.0.0 --hedef-sistem-kimligi <kimlik>
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from medakis.config import ConfigError  # noqa: E402
from medakis.domain.errors import DomainError  # noqa: E402
from medakis.repositories.credentials import CredentialError  # noqa: E402
from medakis.services.seed_service import (  # noqa: E402
    DEFAULT_GENERATOR,
    GENERATORS,
    WRITE_CONFIRMATION_REQUIRED,
    SeedService,
)

DEFAULT_DAYS = {"1.0.0": 28, "2.0.0": 112}


def parse(argv):
    parser = argparse.ArgumentParser(description="MedAkis sentetik veri yönetimi")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--database", required=True)
        if name == "apply":
            p.add_argument("--seed", type=int, default=42)
            p.add_argument("--days", type=int, default=None, help="Varsayılan: 1.0.0 için 28, 2.0.0 için 112")
            p.add_argument("--sifirla", action="store_true", help="Mevcut operasyonel veriyi değiştir")
            p.add_argument("--onay", default=None, help="Veritabanı adı (yazılı onay)")
            p.add_argument("--uretici", default=DEFAULT_GENERATOR, choices=sorted(GENERATORS), help="Üretici sürümü")
            p.add_argument("--hedef-sistem-kimligi", default=None, help="2.0.0: hedef sunucunun sistem kimliği")
    return parser.parse_args(argv)


def confirmation(args) -> str | None:
    needed = args.sifirla or args.database in WRITE_CONFIRMATION_REQUIRED
    if not needed or args.onay is not None:
        return args.onay
    if not sys.stdin.isatty():
        return None
    try:
        return input(f"Onay için veritabanı adını aynen yazın ({args.database}): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None


def main(argv) -> int:
    args = parse(argv)
    try:
        if args.command == "status":
            service = SeedService(args.database)
            s = service.status()
            print(f"Veritabanı: {s.database}")
            print(f"Sunucu sistem kimliği: {s.system_identifier or '-'}")
            # Bilgi amaçlı katalog izi; 2.0.0 yüklemesi ayrıca kesin Alembic revizyonunu doğrular.
            print(f"Şema 0004 katalog izleri: {'var' if s.schema_0004 else 'yok'}")
            print(f"Operasyonel satır: {sum(s.operational_counts.values())}")
            print(f"Korunan tarihsel satır: {s.protected_counts}")
            print(f"İçerik özeti: {s.content_sha256 or '-'}")
            if s.schema_0004:
                print(f"İçerik özeti (0004 kolonlarıyla): {s.content_sha256_0004 or '-'}")
            recorded = (s.last_seed or {}).get("content_sha256")
            match = bool(recorded and recorded in (s.content_sha256, s.content_sha256_0004))
            print(f"Son seed kaydı: {'eşleşiyor' if match else ('yok' if not s.last_seed else 'eşleşmiyor')}")
            print(f"Seed yazma yetkisi: {'AÇIK' if s.write_open else 'kapalı'}")
            return 0
        service = SeedService(
            args.database, generator_version=args.uretici, expected_system_identifier=args.hedef_sistem_kimligi
        )
        days = args.days if args.days is not None else DEFAULT_DAYS[args.uretici]
        params = service.spec.params_type(seed=args.seed, days=days)
        result = service.apply(params, reset=args.sifirla, confirm=confirmation(args))
        label = {"loaded": "yüklendi", "reset": "sıfırlanıp yüklendi", "unchanged": "zaten güncel, değişiklik yok"}
        print(f"{result.database}: {label[result.action]} ({sum(result.row_counts.values())} satır, {result.seconds:.1f} sn)")
        print(f"Üretici: {result.generator_version}")
        print(f"İçerik özeti: {result.content_sha256}")
        return 0
    except (DomainError, ConfigError, CredentialError) as exc:
        print(f"HATA: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
