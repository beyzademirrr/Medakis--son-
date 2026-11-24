"""SQLAlchemy motorları.

Uygulama yalnız düşük yetkili rollerle bağlanır (runtime_engine). medakis_owner yalnız
migration içindir (migration_engine). Süper kullanıcı için motor yoktur. Parola URL'ye
konmaz; bağlantı anında kimlik deposundan alınır.
"""

from __future__ import annotations

from sqlalchemy import URL, Engine, create_engine, event

from medakis.config import ALLOWED_DATABASES, ConfigError, Settings, load_settings
from medakis.repositories.credentials import APP_ROLE, OWNER_ROLE, RAG_ROLE, SEED_ROLE, get_password

RUNTIME_ROLES = frozenset({APP_ROLE, RAG_ROLE})
# medakis_demo seed aracıyla sıfırlanamaz; bu liste veritabanındaki CONNECT yetkisiyle aynıdır.
SEEDABLE_DATABASES = frozenset({"medakis_dev", "medakis_test"})


def _make_engine(role: str, database: str, settings: Settings, allow_prompt: bool) -> Engine:
    if database not in ALLOWED_DATABASES:
        raise ConfigError(f"Bilinmeyen veritabanı: {database!r}")
    url = URL.create(
        "postgresql+psycopg",
        username=role,
        host=settings.pg_host,
        port=settings.pg_port,
        database=database,
    )
    engine = create_engine(
        url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
        hide_parameters=True,  # SQL parametreleri hata mesajlarına ve günlüklere yazılmaz.
        connect_args={"connect_timeout": 5, "application_name": f"medakis:{role}"},
    )
    prompted: list[str] = []

    @event.listens_for(engine, "do_connect")
    def _provide_password(dialect, conn_rec, cargs, cparams):  # noqa: ANN001
        # Gizli giriş kullanıldıysa aynı motorda her yeni bağlantı için yeniden sorma.
        if not prompted:
            prompted.append(get_password(role, allow_prompt=allow_prompt))
        cparams["password"] = prompted[0]

    return engine


def runtime_engine(
    role: str = APP_ROLE,
    database: str | None = None,
    settings: Settings | None = None,
    allow_prompt: bool = False,
) -> Engine:
    """Uygulama bağlantısı. Owner rolü burada kabul edilmez."""
    if role not in RUNTIME_ROLES:
        raise ConfigError(f"Uygulama bağlantısında {role!r} rolü kullanılamaz: {sorted(RUNTIME_ROLES)}")
    settings = settings or load_settings()
    return _make_engine(role, database or settings.pg_database, settings, allow_prompt)


def migration_engine(database: str, settings: Settings | None = None, allow_prompt: bool = True) -> Engine:
    """Yalnız Alembic içindir. Hedef veritabanı açıkça verilmek zorundadır."""
    return _make_engine(OWNER_ROLE, database, settings or load_settings(), allow_prompt)


def seed_engine(database: str, settings: Settings | None = None, allow_prompt: bool = True) -> Engine:
    """Yalnız seed servisi içindir. medakis_demo bağlantı kurulmadan reddedilir."""
    if database not in SEEDABLE_DATABASES:
        raise ConfigError(f"Sentetik veri yalnız {sorted(SEEDABLE_DATABASES)} veritabanlarına yüklenir: {database!r}")
    return _make_engine(SEED_ROLE, database, settings or load_settings(), allow_prompt)


def seed_grant_engine(database: str, settings: Settings | None = None, allow_prompt: bool = True) -> Engine:
    """Yalnız seed yazma penceresi içindir (seed_access): owner rolüyle medakis_seed yetkisini açıp kapatır.

    Bu motor tablo verisine dokunmaz; yalnız GRANT/REVOKE ve yetki sorgusu çalıştırır.
    """
    if database not in SEEDABLE_DATABASES:
        raise ConfigError(f"Seed yetki penceresi yalnız {sorted(SEEDABLE_DATABASES)} içindir: {database!r}")
    return _make_engine(OWNER_ROLE, database, settings or load_settings(), allow_prompt)


def revision_read_engine(database: str, settings: Settings | None = None, allow_prompt: bool = True) -> Engine:
    """Yalnız seed servisinin kesin şema revizyonu okuması içindir (alembic_version, salt okunur işlem).

    alembic_version'ı yalnız medakis_owner okuyabilir; seed rolüne yeni yetki verilmez. Bu motor
    tablo verisine yazmaz; çağıran READ ONLY işlem açar ve sunucu kimliğini seed bağlantısıyla eşler.
    """
    if database not in SEEDABLE_DATABASES:
        raise ConfigError(f"Revizyon okuması yalnız {sorted(SEEDABLE_DATABASES)} içindir: {database!r}")
    return _make_engine(OWNER_ROLE, database, settings or load_settings(), allow_prompt)
