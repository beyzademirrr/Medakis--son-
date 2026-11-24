"""Sentetik veri yönetimi: yalnız medakis_dev/medakis_test, açık onay, idempotent, atomik.

Kurallar (FAZ 1.2C kararları ve bağımsız inceleme düzeltmeleri):
- medakis_demo'ya bağlanılmaz (kod listesi + veritabanı CONNECT yetkisi).
- Aynı veri zaten yüklüyse hiçbir şey yazılmaz.
- Farklı veri varsa yalnız açık sıfırlama + veritabanı adının yazılmasıyla değiştirilir.
- medakis_dev'e her yazma yazılı onay ister; sıfırlamadan önce yedek zorunludur. Yedek, seed
  bağlantısıyla aynı PostgreSQL örneğinden alınmalıdır (sunucu kimliği karşılaştırılır).
- medakis_dev'de seed rolü kalıcı olarak salt okunurdur. Yazma, tek bir owner bağlantısında tek
  işlemde yürür: kimlik kontrolü → commit edilmeyen GRANT → SET LOCAL ROLE medakis_seed → yazmalar
  → RESET ROLE → REVOKE → COMMIT (seed_access). Başka oturumlar bu yetkiyi hiçbir anda görmez.
- İşlem geri alınırsa satırlar, denetim kaydı ve yetki değişiklikleri geri alınır; kimlik
  sayaçlarındaki ilerleme ise PostgreSQL gereği geri alınmaz (yalnız ileri gider, boşluk kalabilir).
- Tarihsel tablolarda kayıt varsa sıfırlama durur; audit_events hiç silinmez, yalnız eklenir.
- Silme + yükleme + sayaç + denetim kaydı tek işlemdedir; doğrulama başarısızsa geri alınır.
  İşlem boyunca operasyonel ve tarihsel tablolar başka yazıcılara kilitlidir; sayaçlar yalnız ileri gider.

Üretici sürümü (G2): varsayılan 1.0.0'dır ve davranışı değişmez (revizyon 0003 kolonlarıyla özet).
2.0.0 yalnız medakis_test'e (gerçek veya izole prova kümesi) yüklenir:
- medakis_dev ve medakis_demo bağlantı kurulmadan reddedilir (yükleme de sıfırlama da).
- Hedef yalnız adla değil bağlantıyla doğrulanır: current_database() ve beklenen sunucu sistem
  kimliği (pg_control_system) hem ön kontrolde hem yazma işleminin içinde karşılaştırılır.
- Şema revizyonu KESİN doğrulanır: alembic_version tam olarak 0004 olmalıdır. Tabloyu seed rolü
  okuyamadığı için ayrı, salt okunur bir medakis_owner bağlantısı kullanılır (yeni yetki yok); bu
  bağlantının da aynı sunucu ve veritabanı olduğu sunucu kimliğiyle doğrulanır. Revizyon okunamazsa,
  birden çok satır varsa veya farklıysa yazma reddedilir (belirsizlik = ret).
- Ek olarak katalog izleri denetlenir (ön kontrol ve yazma işlemi içinde): üç kolon doğru türde ve
  NULL olabilir, beş kısıt doğru tabloya bağlı ve doğrulanmış (NOT VALID değil).
- Rol, yetki ve üyelik değişmez; yazma medakis_test'teki mevcut kalıcı seed yetkisiyle yapılır.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.repositories import audit_repo, seed_access, seed_repo
from medakis.repositories.database import SEEDABLE_DATABASES, revision_read_engine, seed_engine, seed_grant_engine
from medakis.repositories.schema import (
    CONSTRAINTS_0004,
    REVISION_0004,
    SCHEMA_0004_COLUMNS,
    SEED_PERMANENT_WRITE_DATABASES,
    SEED_RESET_TABLES,
)
from medakis.synthetic.canonical import content_sha256
from medakis.synthetic.generator import generate
from medakis.synthetic.params import GENERATOR_VERSION, SyntheticParams
from medakis.synthetic.v2 import GENERATOR_VERSION as GENERATOR_VERSION_V2
from medakis.synthetic.v2 import ParamsV2, generate_v2
from medakis.synthetic.v2.checks import validate_v2
from medakis.synthetic.validate import validate

ROOT = Path(__file__).resolve().parents[3]
SEED_ACTION = "synthetic_seed_applied"
SEED_ACTOR = "seed_tool"
BACKUP_REQUIRED = frozenset({"medakis_dev"})
WRITE_CONFIRMATION_REQUIRED = frozenset({"medakis_dev"})
DEFAULT_GENERATOR = GENERATOR_VERSION  # 1.0.0
# 2.0.0'ın yüklenebildiği tek ad (gerçek veya izole prova kümesinde). medakis_dev/medakis_demo olamaz.
V2_DATABASES = frozenset({"medakis_test"})
NEVER_V2 = frozenset({"medakis_dev", "medakis_demo"})
SYSTEM_IDENTIFIER = re.compile(r"^[0-9]{1,20}$")


@dataclass(frozen=True)
class GeneratorSpec:
    version: str
    extended: bool  # özet ve okuma revizyon 0004 kolonlarını içerir mi
    params_type: type
    generate: Callable[[Any], Any]
    validate: Callable[[Any], list[str]]


GENERATORS: dict[str, GeneratorSpec] = {
    GENERATOR_VERSION: GeneratorSpec(GENERATOR_VERSION, False, SyntheticParams, generate, validate),
    GENERATOR_VERSION_V2: GeneratorSpec(GENERATOR_VERSION_V2, True, ParamsV2, generate_v2, validate_v2),
}


# information_schema.columns.data_type değerleri (revizyon 0004 kolonları)
EXPECTED_0004_TYPES = {
    ("encounters", "triage_category"): "text",
    ("care_tasks", "cancelled_at"): "timestamp with time zone",
    ("care_tasks", "cancel_reason"): "text",
}
assert set(EXPECTED_0004_TYPES) == {(t, c) for t, cols in SCHEMA_0004_COLUMNS.items() for c in cols}


def require_revision_0004(revision: str | None) -> None:
    """Kesin kanıt: alembic_version tam olarak 0004. Okunamayan veya bilinmeyen durum da ret."""
    if revision != REVISION_0004:
        shown = revision or "okunamadı"
        raise DomainError(
            errors.SEED_SCHEMA_REVISION,
            f"Üretici 2.0.0 için Alembic revizyonu {REVISION_0004} olmalı (bulunan: {shown}); hiçbir şey yazılmadı.",
        )


def require_schema_0004(state: seed_repo.SchemaState) -> None:
    """Katalog izleri (ikincil): kolon türü ve NULL olabilirliği, kısıtın tablosu ve doğrulanmışlığı."""
    problems = []
    for key, dtype in EXPECTED_0004_TYPES.items():
        found = state.columns.get(key)
        if found is None:
            problems.append(f"eksik kolon: {key}")
        elif found[0] != dtype:
            problems.append(f"yanlış tür: {key} {found[0]}")
        elif not found[1]:
            problems.append(f"NULL olamayan kolon: {key}")
    for name, (table, _sql) in CONSTRAINTS_0004.items():
        found_ck = state.constraints.get(name)
        if found_ck is None:
            problems.append(f"eksik kısıt: {name}")
        elif found_ck[0] != table:
            problems.append(f"kısıt başka tabloda: {name} ({found_ck[0]})")
        elif not found_ck[1]:
            problems.append(f"doğrulanmamış (NOT VALID) kısıt: {name}")
    if problems:
        raise DomainError(
            errors.SEED_SCHEMA_REVISION,
            f"Üretici 2.0.0 için veritabanı revizyon {REVISION_0004} olmalı ({'; '.join(problems)}); hiçbir şey yazılmadı.",
        )


def require_v2_target(database: str, connected_database: str, server_identity: str, expected: str | None) -> None:
    """Bağlantının gerçekten beklenen hedef olduğunu doğrular (ad + sunucu sistem kimliği)."""
    if database not in V2_DATABASES or connected_database not in V2_DATABASES or connected_database != database:
        raise DomainError(errors.SEED_GENERATOR_NOT_ALLOWED, "Üretici 2.0.0 yalnız medakis_test'e yüklenir; işlem durduruldu.")
    if not expected or server_identity.split(":", 1)[0] != expected:
        raise DomainError(
            errors.SEED_TARGET_UNVERIFIED,
            "Bağlanılan sunucunun sistem kimliği beklenen hedefle aynı değil; hiçbir şey yazılmadı.",
        )


@dataclass(frozen=True)
class SeedStatus:
    database: str
    operational_counts: dict[str, int]
    protected_counts: dict[str, int]
    content_sha256: str | None
    last_seed: dict[str, Any] | None
    write_open: bool  # medakis_seed'in kalıcı yazma/kilit yetkisi var mı (medakis_dev'de her zaman False olmalı)
    system_identifier: str | None = None  # 2.0.0 hedef doğrulaması için (gizli bilgi değildir)
    schema_0004: bool = False
    content_sha256_0004: str | None = None  # 0004 kolonlarıyla özet (yalnız şema 0004 ise)


@dataclass(frozen=True)
class SeedResult:
    database: str
    action: str  # loaded | reset | unchanged
    content_sha256: str
    row_counts: dict[str, int]
    seconds: float
    generator_version: str = GENERATOR_VERSION


def run_backup_script(database: str, server_identity: str) -> None:
    """Yedeği alır; betik, yedeklenecek örneğin kimliği beklenenden farklıysa yedek almadan durur (F4)."""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts" / "db" / "backup.ps1"), "-Database", database, "-BeklenenKimlik", server_identity],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    if proc.returncode != 0:
        raise DomainError(errors.SEED_BACKUP_FAILED, "Yedek alınamadı veya başka bir sunucuya ait; sıfırlama yapılmadı.")


class SeedService:
    def __init__(
        self,
        database: str,
        engine: Engine | None = None,
        backup: Callable[[str, str], None] = run_backup_script,
        grant_engine: Engine | None = None,
        lock_timeout_ms: int = 10_000,
        backup_required: bool | None = None,
        generator_version: str = DEFAULT_GENERATOR,
        expected_system_identifier: str | None = None,
        revision_engine: Engine | None = None,
    ) -> None:
        if database not in SEEDABLE_DATABASES:
            raise DomainError(errors.SEED_DATABASE_NOT_ALLOWED, f"Sentetik veri bu veritabanına yüklenemez: {database}")
        if generator_version not in GENERATORS:
            raise DomainError(errors.SEED_GENERATOR_NOT_ALLOWED, f"Bilinmeyen üretici sürümü: {generator_version}")
        self.spec = GENERATORS[generator_version]
        if self.spec.extended:
            # Bağlantı kurulmadan: medakis_dev/medakis_demo için 2.0.0 yükleme ve sıfırlama reddedilir.
            if database in NEVER_V2 or database not in V2_DATABASES:
                raise DomainError(
                    errors.SEED_GENERATOR_NOT_ALLOWED,
                    f"Üretici {generator_version} {database} veritabanına yüklenemez (yalnız medakis_test).",
                )
            if not expected_system_identifier or not SYSTEM_IDENTIFIER.fullmatch(expected_system_identifier):
                raise DomainError(
                    errors.SEED_TARGET_UNVERIFIED,
                    "Üretici 2.0.0 için hedef sunucunun sistem kimliği açıkça verilmelidir (seed.py status gösterir).",
                )
        self.expected_system_identifier = expected_system_identifier
        self._revision_engine = revision_engine
        self.database = database
        self._engine = engine or seed_engine(database)
        self._backup = backup
        self._grant_engine = grant_engine
        self._lock_timeout_ms = lock_timeout_ms
        self._backup_required = database in BACKUP_REQUIRED if backup_required is None else backup_required

    def _check_database(self, conn) -> None:  # noqa: ANN001
        if seed_repo.current_database(conn) != self.database:
            raise DomainError(errors.SEED_WRONG_DATABASE, "Bağlantı beklenen veritabanına ait değil; işlem durduruldu.")

    def _check_target(self, conn) -> None:  # noqa: ANN001
        """2.0.0: bağlantı hedefi ve şema revizyonu (her bağlantıda, yazmadan önce)."""
        if not self.spec.extended:
            return
        require_v2_target(
            self.database, seed_repo.current_database(conn), seed_repo.server_identity(conn), self.expected_system_identifier
        )
        require_schema_0004(seed_repo.schema_state(conn))

    def _verify_revision(self, identity: str) -> None:
        """2.0.0: kesin revizyon, ayrı ve salt okunur owner bağlantısıyla; aynı sunucu ve veritabanı olmalı."""
        if not self.spec.extended:
            return
        engine = self._revision_engine or revision_read_engine(self.database)
        with engine.connect() as conn:
            with conn.begin():
                seed_repo.set_read_only(conn)
                if seed_repo.current_database(conn) != self.database or seed_repo.server_identity(conn) != identity:
                    raise DomainError(
                        errors.SEED_TARGET_UNVERIFIED,
                        "Revizyon bağlantısı seed bağlantısıyla aynı sunucu/veritabanı değil; hiçbir şey yazılmadı.",
                    )
                revision = seed_repo.alembic_revision(conn)
        require_revision_0004(revision)

    def _digest(self, conn) -> str:  # noqa: ANN001
        extended = self.spec.extended
        return content_sha256(seed_repo.read_tables(conn, extended=extended), extended=extended)

    def status(self) -> SeedStatus:
        with self._engine.connect() as conn:
            self._check_database(conn)
            ops = seed_repo.operational_counts(conn)
            protected = seed_repo.protected_counts(conn)
            digest = content_sha256(seed_repo.read_tables(conn)) if any(ops.values()) else None
            events = audit_repo.list_events(conn, SEED_ACTION)
            write_open = seed_access.write_open(conn)
            identity = seed_repo.server_identity(conn)
            try:
                require_schema_0004(seed_repo.schema_state(conn))
                schema_0004 = True
            except DomainError:
                schema_0004 = False
            digest_0004 = (
                content_sha256(seed_repo.read_tables(conn, extended=True), extended=True)
                if schema_0004 and any(ops.values())
                else None
            )
        return SeedStatus(self.database, ops, protected, digest, events[-1]["detail_json"] if events else None,
                          write_open, identity.split(":", 1)[0], schema_0004, digest_0004)  # fmt: skip

    def apply(self, params: Any = None, reset: bool = False, confirm: str | None = None) -> SeedResult:
        started = time.perf_counter()
        params = params or self.spec.params_type()
        if not isinstance(params, self.spec.params_type):
            raise DomainError(errors.SEED_GENERATOR_NOT_ALLOWED, f"Parametreler üretici {self.spec.version} ile uyumlu değil.")
        if self.database in WRITE_CONFIRMATION_REQUIRED and confirm != self.database:
            raise DomainError(errors.SEED_CONFIRMATION_REQUIRED, f"{self.database} için yazılı onay gerekir (veritabanı adı).")
        if reset and confirm != self.database:
            raise DomainError(errors.SEED_CONFIRMATION_REQUIRED, "Sıfırlama için veritabanı adı aynen yazılmalı.")

        dataset = self.spec.generate(params)
        issues = self.spec.validate(dataset)
        if issues:
            raise DomainError(errors.SEED_INVALID_DATASET, f"Üretilen veri kurallardan geçmedi ({len(issues)} ihlal).")
        target = dataset.content_sha256()

        # 1) Salt okunur ön kontrol
        with self._engine.connect() as conn:
            self._check_database(conn)
            self._check_target(conn)
            self._refuse_if_history(seed_repo.protected_counts(conn))
            ops = seed_repo.operational_counts(conn)
            previous = self._digest(conn) if any(ops.values()) else None
            identity = seed_repo.server_identity(conn)
            stale_grant = self.database not in SEED_PERMANENT_WRITE_DATABASES and seed_access.write_open(conn)
        self._verify_revision(identity)  # 2.0.0: yazmadan önce kesin revizyon (belirsizlik = ret)
        if stale_grant:
            raise DomainError(
                errors.SEED_ROLE_SETUP,
                f"{self.database} içinde medakis_seed'in kalıcı yazma yetkisi var (beklenmiyor; eski bir yedek "
                "geri yüklenmiş olabilir). Önce 'alembic upgrade head' ve yetki kontrolü; hiçbir şey değişmedi.",
            )
        if previous == target:
            return SeedResult(self.database, "unchanged", target, dataset.row_counts(), time.perf_counter() - started,
                              self.spec.version)  # fmt: skip
        if previous is not None and not reset:
            raise DomainError(
                errors.SEED_NEEDS_RESET,
                "Veritabanında farklı operasyonel veri var. Değiştirmek için açık sıfırlama gerekir.",
            )
        if previous is not None and self._backup_required:
            self._backup(self.database, identity)

        # 2) Tek işlem: kilitler, kimlik, (gerekirse işlem içi yetki), yeniden kontrol, sil, yükle, sayaç, doğrula, denetim
        try:
            if self.database in SEED_PERMANENT_WRITE_DATABASES:
                action = self._write_with_permanent_grant(dataset, params, target, previous, identity)
            else:
                action = self._write_with_scoped_grant(dataset, params, target, previous, identity)
        except OperationalError as exc:
            if isinstance(exc.orig, psycopg.errors.LockNotAvailable):
                raise DomainError(
                    errors.SEED_LOCKED,
                    "Tablolar başka bir işlem tarafından kullanılıyor; seed durduruldu, hiçbir şey değişmedi.",
                ) from None
            raise
        return SeedResult(self.database, action, target, dataset.row_counts(), time.perf_counter() - started,
                          self.spec.version)  # fmt: skip

    def _guard(self, conn, identity: str) -> None:  # noqa: ANN001
        self._check_database(conn)
        if seed_repo.server_identity(conn) != identity:
            raise DomainError(errors.SEED_SERVER_MISMATCH, "Bağlantı ön kontroldeki sunucuya ait değil; işlem durduruldu.")
        self._check_target(conn)  # 2.0.0: aynı işlem içinde hedef ve şema yeniden doğrulanır

    def _write_with_permanent_grant(self, dataset: Any, params: Any, target: str, previous: str | None, identity: str) -> str:
        """medakis_test: seed rolünün kalıcı yetkisiyle, seed bağlantısında."""
        with self._engine.begin() as conn:
            seed_repo.lock(conn, self._lock_timeout_ms)
            self._guard(conn, identity)
            return self._load(conn, dataset, params, target, previous)

    def _write_with_scoped_grant(self, dataset: Any, params: Any, target: str, previous: str | None, identity: str) -> str:
        """medakis_dev: owner bağlantısında tek işlem; yetki commit edilmez (T1/T2). Yalnız 1.0.0."""
        if self.spec.extended:  # ikinci savunma hattı: yapıcı zaten reddeder
            raise DomainError(errors.SEED_GENERATOR_NOT_ALLOWED, "Üretici 2.0.0 kapsamlı yetki penceresiyle yüklenemez.")
        engine = self._grant_engine or seed_grant_engine(self.database)
        with engine.begin() as conn:
            self._guard(conn, identity)  # kilit ve yetki değişikliğinden önce sunucu ve veritabanı kimliği
            seed_access.require_membership(conn)
            seed_repo.lock(conn, self._lock_timeout_ms)  # owner: danışma + tablo kilitleri (MAINTAIN gerekmez)
            seed_access.grant_scoped(conn, with_delete=previous is not None)
            seed_access.become_seed(conn)
            action = self._load(conn, dataset, params, target, previous)
            seed_access.leave_seed(conn)
            seed_access.revoke_scoped(conn)
            seed_access.require_idle(conn)
        return action

    def _load(self, conn, dataset: Any, params: Any, target: str, previous: str | None) -> str:  # noqa: ANN001
        """İşlem içinde, seed rolünün yetkisiyle çalışır."""
        self._refuse_if_history(seed_repo.protected_counts(conn))
        ops = seed_repo.operational_counts(conn)
        current = self._digest(conn) if any(ops.values()) else None
        if current == target:
            return "unchanged"  # eşzamanlı başka bir seed aynı veriyi az önce yükledi; hiçbir şey yazılmaz
        if current != previous:
            raise DomainError(errors.SEED_NEEDS_RESET, "Veri ön kontrolden sonra değişti; işlem durduruldu.")
        if current is not None:
            seed_repo.delete_operational(conn)
        for name in SEED_RESET_TABLES:
            seed_repo.insert_rows(conn, name, dataset.tables[name])
        seed_repo.sync_sequences(conn)
        if self._digest(conn) != target:
            raise DomainError(errors.SEED_VERIFY_FAILED, "Yüklenen veri doğrulanamadı; işlem geri alındı.")
        audit_repo.insert_event(
            conn,
            SEED_ACTOR,
            SEED_ACTION,
            "database",
            self.database,
            {
                "database": self.database,
                "reset": current is not None,
                "generator_version": self.spec.version,
                "params_fingerprint": params.fingerprint(),
                "params": params.as_dict(),
                "content_sha256": target,
                "previous_content_sha256": current,
                "row_counts": dataset.row_counts(),
            },
        )
        return "reset" if current is not None else "loaded"

    @staticmethod
    def _refuse_if_history(protected: dict[str, int]) -> None:
        present = {name: n for name, n in protected.items() if n}
        if present:
            listed = ", ".join(f"{name}={n}" for name, n in sorted(present.items()))
            raise DomainError(
                errors.SEED_HISTORY_PRESENT,
                f"Tarihsel kayıtlar var ({listed}); bunlar silinmez, sıfırlama yapılmadı.",
            )
