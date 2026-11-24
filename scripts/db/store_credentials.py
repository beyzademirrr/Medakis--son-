"""Rol parolalarını doğrulayıp Windows Credential Manager'a kaydeder.

Parola gizli girişle alınır, önce 127.0.0.1 üzerinden gerçek girişle sınanır, başarılıysa
kaydedilir. Hiçbir parola yazdırılmaz veya dosyaya yazılmaz.

    .\\.venv\\Scripts\\python.exe scripts\\db\\store_credentials.py            eksik parolaları ister
    .\\.venv\\Scripts\\python.exe scripts\\db\\store_credentials.py --degistir  kayıtlıları da yeniden ister
    .\\.venv\\Scripts\\python.exe scripts\\db\\store_credentials.py --kontrol   yalnız doğrular
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import psycopg  # noqa: E402

from medakis.config import load_settings  # noqa: E402
from medakis.repositories.credentials import (  # noqa: E402
    ALL_ROLES,
    SERVICE,
    CredentialError,
    read_stored_password,
    store_password,
)

CHECK_DATABASE = "medakis_test"
MAX_ATTEMPTS = 3


def can_login(role: str, password: str) -> tuple[bool, str]:
    settings = load_settings()
    try:
        with psycopg.connect(
            host=settings.pg_host,
            port=settings.pg_port,
            dbname=CHECK_DATABASE,
            user=role,
            password=password,
            connect_timeout=5,
        ) as conn:
            conn.execute("SELECT 1")
    except psycopg.OperationalError as exc:
        if "password authentication failed" in str(exc):
            return False, "parola yanlış"
        return False, "sunucuya bağlanılamadı (docker compose ps ile kontrol edin)"
    return True, ""


def main(argv: list[str]) -> int:
    check_only = "--kontrol" in argv
    replace = "--degistir" in argv
    failed = False
    for role in ALL_ROLES:
        try:
            stored = read_stored_password(role)
        except CredentialError as exc:
            print(f"HATA: {exc}")
            return 1

        if stored and not replace:
            ok, why = can_login(role, stored)
            if ok:
                print(f"{role}: kayıtlı ve giriş başarılı.")
                continue
            print(f"{role}: kayıt var ama giriş doğrulanamadı: {why}.")
        elif check_only:
            print(f"{role}: kayıt yok.")
            failed = True
            continue

        if check_only:
            failed = True
            continue

        for _ in range(MAX_ATTEMPTS):
            typed = getpass.getpass(f"{role} parolası (gizli giriş): ")
            ok, why = can_login(role, typed)
            if ok:
                store_password(role, typed)
                if read_stored_password(role) != typed:
                    print(f"HATA: {role} kaydı geri okunamadı.")
                    return 1
                print(f"{role}: giriş başarılı, Credential Manager'a kaydedildi ({SERVICE}).")
                break
            print(f"{role}: {why}.")
        else:
            print(f"{role}: kaydedilmedi.")
            failed = True
    print("Tamamlandı." if not failed else "Eksik kayıtlar var.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
