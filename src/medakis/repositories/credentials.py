"""PostgreSQL rol parolalarına erişim.

Birincil kaynak işletim sisteminin kimlik deposudur (Windows Credential Manager, keyring ile).
Depoya ulaşılamazsa veya kayıt yoksa, yalnız açıkça izin verilmiş ve gerçek bir terminalde
çalışılıyorsa gizli giriş istenir. Parola kaynak kodda, dosyada, ortam değişkeninde veya
günlükte tutulmaz; bu modülün hata mesajları parola içermez.
"""

from __future__ import annotations

import getpass
import sys

import keyring

SERVICE = "medakis-postgres"

OWNER_ROLE = "medakis_owner"
APP_ROLE = "medakis_app"
RAG_ROLE = "medakis_rag"
SEED_ROLE = "medakis_seed"
ALL_ROLES = (OWNER_ROLE, APP_ROLE, RAG_ROLE, SEED_ROLE)


class CredentialError(RuntimeError):
    """Parola alınamadı. Mesaj parola içermez."""


def _check_role(role: str) -> None:
    if role not in ALL_ROLES:
        raise CredentialError(f"Bilinmeyen veritabanı rolü: {role!r}")


def read_stored_password(role: str) -> str | None:
    """Kimlik deposundaki parolayı döndürür; kayıt yoksa None. Depo hatası CredentialError olur."""
    _check_role(role)
    try:
        password = keyring.get_password(SERVICE, role)
    except Exception as exc:  # Arka uç türleri platforma göre değişir.
        raise CredentialError(
            f"Kimlik deposu okunamadı ({type(exc).__name__}). "
            "Windows Credential Manager erişimini kontrol edin."
        ) from None
    return password or None


def store_password(role: str, password: str) -> None:
    _check_role(role)
    if not password:
        raise CredentialError("Boş parola kaydedilemez.")
    try:
        keyring.set_password(SERVICE, role, password)
    except Exception as exc:
        raise CredentialError(f"Kimlik deposuna yazılamadı ({type(exc).__name__}).") from None


def get_password(role: str, *, allow_prompt: bool = False) -> str:
    """Rol parolasını döndürür.

    allow_prompt=True ise depo başarısız olduğunda terminalden gizli giriş ister.
    Streamlit gibi terminali olmayan süreçlerde giriş istenmez; açık bir hata verilir.
    """
    _check_role(role)
    problem = f"{role} için kimlik deposunda kayıt yok."
    try:
        stored = read_stored_password(role)
    except CredentialError as exc:
        stored, problem = None, str(exc)
    if stored:
        return stored

    if allow_prompt and sys.stdin is not None and sys.stdin.isatty():
        try:
            typed = getpass.getpass(f"{role} parolası (gizli giriş): ")
        except (EOFError, KeyboardInterrupt):
            typed = ""
        if typed:
            return typed
        problem = f"{role} için parola girilmedi."

    raise CredentialError(
        f"{problem} Kayıt için: .\\.venv\\Scripts\\python.exe scripts\\db\\store_credentials.py"
    )
