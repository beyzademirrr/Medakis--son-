"""İzole prova kümesi için rol parola doğrulayıcıları (yalnız scripts/db/prova.ps1 çağırır).

Kimlik deposundaki parolalardan SCRAM-SHA-256 doğrulayıcısı üretir ve ALTER ROLE komutlarını
standart çıktıya yazar; çıktı doğrudan prova konteynerindeki psql'e borulanır. Açık parola
hiçbir zaman çıktıya, dosyaya veya sunucuya gitmez (sunucuya giden, PostgreSQL'in kendi sakladığı
doğrulayıcı biçimidir). Prova ortamı değişkenleri yoksa çalışmaz.
"""

import base64
import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from medakis.repositories.credentials import ALL_ROLES, get_password  # noqa: E402

PROVA_PROJECT = "medakis-prova"


def scram_verifier(password: str, iterations: int = 4096) -> str:
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def main() -> int:
    if os.environ.get("COMPOSE_PROJECT_NAME") != PROVA_PROJECT:
        print("HATA: yalnız prova ortamında çalışır.", file=sys.stderr)
        return 1
    for role in ALL_ROLES:
        print(f"ALTER ROLE {role} PASSWORD '{scram_verifier(get_password(role, allow_prompt=False))}';")
    return 0


if __name__ == "__main__":
    sys.exit(main())
