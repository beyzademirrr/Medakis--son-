"""Canlı testlerin hedef PostgreSQL konteyneri (pytest bunu toplamaz).

Varsayılan: gerçek yerel sunucu (medakis-postgres, medakis_pgdata, 5432). İzole prova kümesi
(scripts/db/prova.ps1) bu değerleri ortam değişkenleriyle değiştirir; docker compose komutları da
COMPOSE_PROJECT_NAME/COMPOSE_FILE ile aynı kümeye gider.
"""

import os

CONTAINER = os.environ.get("MEDAKIS_PG_CONTAINER", "medakis-postgres")
VOLUME = os.environ.get("MEDAKIS_PG_VOLUME", "medakis_pgdata")
PORT = int(os.environ.get("PGPORT", "5432"))
