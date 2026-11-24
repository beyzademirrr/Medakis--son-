"""Süreç sonlandırma testi için yardımcı (pytest bunu toplamaz; test_seed_scoped_grant_live çalıştırır).

medakis_test'i medakis_dev gibi (işlem içi yetki yolu) kullanarak seed başlatır. Gerçek sayaç
güncellemesinden sonra, commit'ten önce "READY" yazar ve bekler; ebeveyn süreci öldürür.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import medakis.services.seed_service as seed_service  # noqa: E402
from medakis.repositories import seed_repo  # noqa: E402
from medakis.repositories.database import seed_engine, seed_grant_engine  # noqa: E402
from medakis.synthetic.params import SyntheticParams  # noqa: E402

seed_service.SEED_PERMANENT_WRITE_DATABASES = frozenset()
real_sync = seed_repo.sync_sequences


def sync_then_hang(conn):
    real_sync(conn)
    print("READY", flush=True)
    time.sleep(600)


seed_repo.sync_sequences = sync_then_hang
service = seed_service.SeedService(
    "medakis_test",
    engine=seed_engine("medakis_test", allow_prompt=False),
    grant_engine=seed_grant_engine("medakis_test", allow_prompt=False),
    backup=lambda database, identity: None,
)
reset = sys.argv[1] == "reset"
service.apply(SyntheticParams(days=3, seed=int(sys.argv[2])), reset=reset, confirm="medakis_test" if reset else None)
print("UNEXPECTED_COMMIT", flush=True)
