"""Aşama 3 uçtan uca doğrulama: servis sonuçları ile bağımsız SQL hesaplarını yan yana koyar. YALNIZ OKUR.

    .\\.venv\\Scripts\\python.exe scripts\\asama3_uctan_uca.py
    .\\.venv\\Scripts\\python.exe scripts\\asama3_uctan_uca.py --gun 2026-10-04 --vardiya GECE --servis 1

Bağlantılar medakis_app rolüyle ve READ ONLY işlemle açılır; veritabanına yazmaz, seed/migration/restore
çalıştırmaz. Varsayılan hedef yapılandırılmış veritabanıdır (medakis_dev). Fark 1e-6'yı aşarsa çıkış kodu 1.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from medakis.config import load_settings  # noqa: E402
from medakis.domain.capacity import staff_capacity  # noqa: E402
from medakis.domain.time import Interval  # noqa: E402
from medakis.repositories import staff_repo, workload_repo  # noqa: E402
from medakis.repositories.database import runtime_engine  # noqa: E402
from medakis.services.workload_service import WorkloadService, read_only_factory  # noqa: E402

TOL = 1e-6

WINDOWS_SQL = """
WITH w AS (SELECT gs AS ws, gs + interval '15 minutes' AS we
           FROM generate_series(CAST(:s AS timestamptz), CAST(:e AS timestamptz) - interval '15 minutes', interval '15 minutes') gs),
t AS (SELECT completed_at - actual_minutes * interval '1 minute' AS ts, completed_at AS te, actual_minutes AS m
      FROM care_tasks WHERE unit_id = :u AND status = 'completed')
SELECT w.ws, coalesce(sum(t.m * extract(epoch FROM least(t.te, w.we) - greatest(t.ts, w.ws)) / extract(epoch FROM t.te - t.ts)), 0)
FROM w LEFT JOIN t ON t.te > w.ws AND t.ts < w.we GROUP BY w.ws ORDER BY w.ws
"""
CAPACITY_SQL = """
WITH plan AS (SELECT tstzrange(CAST(:s AS timestamptz), CAST(:e AS timestamptz), '[)') AS p),
work AS (SELECT s.staff_id, range_agg(tstzrange(s.starts_at, s.ends_at, '[)') * plan.p) AS wm
         FROM staff_shifts s, plan WHERE s.unit_id = :u AND tstzrange(s.starts_at, s.ends_at, '[)') && plan.p GROUP BY s.staff_id),
un AS (SELECT staff_id, range_agg(tstzrange(starts_at, ends_at, '[)')) AS um FROM staff_unavailability GROUP BY staff_id)
SELECT st.role, sum((SELECT coalesce(sum(extract(epoch FROM upper(r) - lower(r))), 0) / 60
                     FROM unnest(w.wm - coalesce(un.um, '{}'::tstzmultirange)) r))
FROM work w JOIN staff st ON st.id = w.staff_id LEFT JOIN un USING (staff_id) GROUP BY st.role ORDER BY st.role
"""
PENDING_SQL = """
SELECT coalesce(sum(estimated_minutes), 0) FROM care_tasks WHERE unit_id = :u AND created_at <= :t
AND (status = 'pending' OR (status = 'completed' AND completed_at > :t))
"""
PATIENTS_SQL = """
SELECT count(*) FROM encounters WHERE unit_id = :u AND admitted_at <= :t AND (discharged_at IS NULL OR discharged_at > :t)
"""
QUALITY_SQL = """
SELECT count(*) FILTER (WHERE status = 'completed'),
       count(*) FILTER (WHERE status = 'completed' AND completed_at - actual_minutes * interval '1 minute' < created_at),
       count(*) FILTER (WHERE status = 'completed' AND completed_at = created_at),
       count(*) FILTER (WHERE status = 'cancelled')
FROM care_tasks
"""


def row(label: str, service: float, oracle: float) -> bool:
    diff = abs(service - oracle)
    ok = diff <= TOL
    print(f"| {label} | {service:.4f} | {oracle:.4f} | {diff:.2e} | {'eşit' if ok else 'FARKLI'} |")
    return ok


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Aşama 3 uçtan uca doğrulama (yalnız okur)")
    parser.add_argument("--gun", default="2026-10-04")
    parser.add_argument("--vardiya", default="GECE")
    parser.add_argument("--servis", type=int, default=1)
    args = parser.parse_args(argv)
    settings = load_settings()
    tz = ZoneInfo(settings.timezone)
    engine = runtime_engine("medakis_app", settings.pg_database)
    connect = read_only_factory(engine)
    service = WorkloadService(connect=connect, timezone=settings.timezone)
    options = service.options()
    template = next(t for t in options.templates if t.name == args.vardiya)
    local_day = date.fromisoformat(args.gun)
    snap = service.unit_snapshot(args.servis, local_day, template.id)
    s, e = snap.shift.start, snap.shift.end
    print(f"Veritabanı: {settings.pg_database} (medakis_app, READ ONLY) · servis {snap.unit.name} · {args.gun} {template.name}")
    print(f"Vardiya (UTC): {s.isoformat()} – {e.isoformat()} · kapsam: {options.coverage.source}\n")
    print("| Ölçü | Servis | Bağımsız SQL | Fark | Sonuç |\n|---|---|---|---|---|")
    ok = True
    with connect() as conn:
        windows = [float(v) for _, v in conn.execute(text(WINDOWS_SQL), {"s": s, "e": e, "u": args.servis})]
        ok &= row("Gerçekleşen iş, vardiya toplamı (dk)", snap.observed_total.value or 0.0, sum(windows))
        for index in (0, 15, 16, 47):
            local = snap.windows[index].window.start.astimezone(tz).strftime("%d.%m %H:%M")
            ok &= row(f"Pencere {local} (dk)", snap.windows[index].observed_minutes or 0.0, windows[index])
        roles = {r: float(m) for r, m in conn.execute(text(CAPACITY_SQL), {"s": s, "e": e, "u": args.servis})}
        ok &= row("Kapasite, toplam (dk)", snap.capacity_total.value or 0.0, sum(roles.values()))
        for role, minutes in roles.items():
            ok &= row(f"Kapasite, {role} (dk)", snap.capacity_by_role.get(role, 0.0), minutes)
        ok &= row("Bekleyen iş, 20:00 (dk)", snap.pending.value or 0.0, float(conn.execute(text(PENDING_SQL), {"u": args.servis, "t": s}).scalar_one()))
        ok &= row("Hasta sayısı, 20:00", snap.patient_count.value or 0.0, float(conn.execute(text(PATIENTS_SQL), {"u": args.servis, "t": s}).scalar_one()))

        # Gece yarısı: aynı vardiyanın iki yerel güne düşen kısımlarının toplamı vardiya toplamına eşit.
        midnight = next(w.window.start for w in snap.windows if w.window.start.astimezone(tz).strftime("%H:%M") == "00:00")
        before = sum(w.observed_minutes or 0 for w in snap.windows if w.window.start < midnight)
        after = sum(w.observed_minutes or 0 for w in snap.windows if w.window.start >= midnight)
        ok &= row("Gece yarısı: önceki gün + sonraki gün = toplam (dk)", before + after, snap.observed_total.value or 0.0)

        # Bir personelin elle gösterimi.
        shifts = staff_repo.list_shifts(conn, args.servis, s, e)
        people = {p.id: p for p in workload_repo.staff_by_ids(conn, [x.staff_id for x in shifts])}
        unavailable = workload_repo.unavailability_overlapping(conn, list(people), s, e)
        nurse_shift = next(x for x in shifts if people[x.staff_id].role == "nurse" and x.starts_at == s)
        person = staff_capacity(people[nurse_shift.staff_id], shifts, unavailable, Interval(s, e))
        print(f"\nElle örnek: {person.synthetic_code} vardiya {person.work_minutes:.0f} dk − mola {person.break_minutes:.0f} dk "
              f"− izin/diğer {person.other_unavailable_minutes:.0f} dk = {person.net_minutes:.0f} dk "
              f"(break_minutes özeti {nurse_shift.break_minutes} dk, düşülmedi)")
        completed, before_created, same_instant, cancelled = conn.execute(text(QUALITY_SQL)).one()
    print(f"\nVeri kalitesi (tüm veri): tamamlanan {completed}; türetilen başlangıcı oluşturulmadan önce {before_created} "
          f"(%{100 * before_created / completed:.1f}); oluşturulma = tamamlanma {same_instant}; iptal (zamanı yok) {cancelled}.")
    print(f"Bu vardiya: tamamlanan katkı {snap.quality.completed_in_shift}, K2 işaretli {snap.quality.realized_before_created}, "
          f"20:00'de durumu belirsiz iptal {snap.quality.uncertain_cancelled} ({snap.quality.uncertain_cancelled_minutes:.1f} dk).")
    engine.dispose()
    print("\nSONUÇ: " + ("bütün değerler eşit" if ok else "FARK VAR"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
