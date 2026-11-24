"""Aşama 3 servis paneli: seçilen servis, tarih ve vardiya için iş yükü ve personel kapasitesi.

UI'da SQL veya hesap yoktur; yalnız WorkloadService çağrılır. Eksik veri "—" ile ve nedeniyle,
gerçek sıfır "0" ile ve nedeniyle gösterilir. Tahmin kartı bilinçli ertelendi: bu panel 1.0.0 verisini okur, Aşama 4
modelleri 2.0.0 sözleşmesiyle eğitildi; tahmin ve ihtiyaç hesabı Öneri inceleme ekranındadır.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from medakis.services.workload_service import Metric, PanelOptions, UnitSnapshot, WorkloadService, default_service

ROLE_LABELS = {"nurse": "Hemşire", "support": "Destek personeli", "supervisor": "Sorumlu"}
KIND_LABELS = {"inpatient": "yataklı", "emergency": "acil", "outpatient": "ayaktan"}
STATUS_HINT = {"missing": "Veri yok", "zero": "Gerçek sıfır", "partial": "Kısmi kapsam", "ok": ""}


@st.cache_resource(show_spinner=False)
def _default_service() -> WorkloadService:
    return default_service()


# Testler bu sağlayıcıyı değiştirerek veritabanı olmadan paneli çalıştırır.
service_provider: Callable[[], WorkloadService] = _default_service


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def metric_value(metric: Metric, unit: str = "dk") -> str:
    if metric.value is None:
        return "—"
    number = f"{metric.value:,.0f}".replace(",", ".") if unit else f"{metric.value:.0f}"
    text = f"{number} {unit}".strip()
    return f"{text} (kısmi)" if metric.status == "partial" else text


def metric_help(metric: Metric) -> str | None:
    parts = [STATUS_HINT.get(metric.status, ""), metric.note]
    joined = ". ".join(p for p in parts if p)
    return joined or None


def _local(value: datetime, tz: ZoneInfo) -> str:
    return value.astimezone(tz).strftime("%d.%m.%Y %H:%M")


def _coverage_days(options: PanelOptions) -> tuple[date, date]:
    tz = ZoneInfo(options.timezone)
    first = options.coverage.start.astimezone(tz).date()  # type: ignore[union-attr]
    last = (options.coverage.end - timedelta(microseconds=1)).astimezone(tz).date()  # type: ignore[operator]
    return first, last


def render(provider: Callable[[], WorkloadService] | None = None) -> None:
    st.subheader("Servis paneli")
    st.caption("İş yükü ve kapasite birimi: personel-dakikası. Görev süreleri ve katsayılar sentetik simülasyon varsayımıdır.")
    try:
        service = (provider or service_provider)()
        options = service.options()
    except Exception as exc:  # noqa: BLE001 - bağlantı/kimlik hatası panelde açıklanır
        st.error(
            "Veritabanına bağlanılamadı; panel verisi gösterilemiyor. PostgreSQL konteynerinin çalıştığını ve "
            "uygulama rolü parolasının kayıtlı olduğunu kontrol edin (README: PostgreSQL kurulumu). "
            f"Ayrıntı: {type(exc).__name__}"
        )
        return
    if not options.has_data:
        st.info(
            "Veritabanında sentetik operasyonel veri yok. Veri, proje sahibinin onayıyla `scripts/db/seed.py` ile "
            "yüklenir. Bu ekranda sıfır veya tahmini değer uydurulmaz."
        )
        return
    _render_with_data(service, options)


def _render_with_data(service: WorkloadService, options: PanelOptions) -> None:
    tz = ZoneInfo(options.timezone)
    first, last = _coverage_days(options)
    units = list(options.units)
    templates = list(options.templates)
    col_unit, col_day, col_shift = st.columns(3)
    unit = col_unit.selectbox(
        "Servis", units, format_func=lambda u: f"{u.name} ({KIND_LABELS.get(u.kind, u.kind)})", key="panel_unit"
    )
    default_day = min(first + timedelta(days=3), last)
    local_day = col_day.date_input("Tarih (yerel)", value=default_day, min_value=first, max_value=last, key="panel_day")
    template = col_shift.selectbox(
        "Vardiya",
        templates,
        format_func=lambda t: f"{t.name} {t.start_local.strftime('%H:%M')} ({t.duration_minutes // 60} sa)",
        index=next((i for i, t in enumerate(templates) if t.name == "GECE"), 0),
        key="panel_template",
    )
    shift = service.shift_interval(template, local_day)
    steps = []
    moment = shift.start
    while moment < shift.end:  # type: ignore[operator]
        steps.append(moment)
        moment += timedelta(minutes=15)
    at = st.select_slider(
        "Değerlendirme anı (bekleyen iş ve hasta sayısı bu ana göredir)",
        options=steps,
        value=steps[0],
        format_func=lambda m: m.astimezone(tz).strftime("%d.%m %H:%M"),
        key="panel_at",
    )
    snap = service.unit_snapshot(unit.id, local_day, template.id, at=at)
    render_snapshot(snap)


def render_snapshot(snap: UnitSnapshot) -> None:
    tz = ZoneInfo(snap.timezone)
    st.markdown(
        f"**{snap.unit.name}** · {snap.template.name} vardiyası · {_local(snap.shift.start, tz)} – "
        f"{_local(snap.shift.end, tz)} (yerel saat, {snap.timezone})"
    )
    for note in snap.notes:
        st.warning(note)

    cards = st.columns(5)
    cards[0].metric("Hasta sayısı (seçilen an)", metric_value(snap.patient_count, ""), help=metric_help(snap.patient_count))
    cards[1].metric("Bekleyen iş (seçilen an)", metric_value(snap.pending), help=metric_help(snap.pending))
    cards[2].metric("Kullanılabilir personel (vardiya)", metric_value(snap.capacity_total), help=metric_help(snap.capacity_total))
    if snap.primary_role:
        cards[3].metric(
            f"{role_label(snap.primary_role)} kapasitesi (vardiya)",
            metric_value(snap.primary_role_capacity),
            help=metric_help(snap.primary_role_capacity),
        )
    else:
        cards[3].metric("Rol vurgusu", "—", help="Bu servis türü için vurgulanan rol yok; rol kırılımı aşağıda.")
    cards[4].metric("Gerçekleşen iş (vardiya)", metric_value(snap.observed_total), help=metric_help(snap.observed_total))
    st.info("30/60 dakikalık tahmin modelleri Aşama 4'te tamamlandı, ancak bu panelde gösterilmez: panel 1.0.0 verisini okur, "
            "modeller 2.0.0 veri sözleşmesiyle eğitildi (tahmin kartı veri geçişi kararına kadar ertelendi). Tahmin ve "
            "ihtiyaç hesabı 'Öneri inceleme ve onay' sekmesindedir.")
    if snap.measured is not None and snap.planned_full_shift_capacity is not None:
        covered = sum(1 for w in snap.windows if w.capacity_minutes is not None)
        st.caption(
            f"Kısmi kapsam: kapasite, gerçekleşen iş, grafik ve tablolar yalnız veri kapsamındaki "
            f"{_local(snap.measured.start, tz)} – {_local(snap.measured.end, tz)} aralığı için hesaplandı "
            f"({covered}/{len(snap.windows)} pencere); kalan saatler ölçülmedi."
        )
        st.caption(
            f"Tam vardiya planlı kapasitesi: {metric_value(snap.planned_full_shift_capacity)} "
            "(kayıtlı vardiya ve uygunsuzluklardan, ölçülmemiş saatler dahil; ölçülmüş kapasite değildir, "
            "gerçekleşen işle karşılaştırılmaz)."
        )

    for metric, label in ((snap.pending, "Bekleyen iş"), (snap.capacity_total, "Kapasite"), (snap.observed_total, "Gerçekleşen iş")):
        if metric.status in ("missing", "zero", "partial") and metric.note:
            st.caption(f"{label}: {metric.note}")
    if snap.quality.uncertain_cancelled:
        st.caption(
            f"Sınırlama: İptal zamanı kaydedilmediği için {snap.quality.uncertain_cancelled} iptal görevin "
            f"({snap.quality.uncertain_cancelled_minutes:.0f} dk) seçilen anda açık olup olmadığı bilinmiyor. "
            "Bekleyen işe katılmadılar; geçmiş durumları kesin değildir."
        )

    st.markdown("#### 15 dakikalık gerçekleşen iş ve kullanılabilir kapasite")
    chart = pd.DataFrame(
        {
            "Gerçekleşen iş (dk)": [w.observed_minutes for w in snap.windows],
            "Kullanılabilir kapasite (dk)": [w.capacity_minutes for w in snap.windows],
            **(
                {f"{role_label(snap.primary_role)} kapasitesi (dk)": [w.primary_role_capacity for w in snap.windows]}
                if snap.primary_role
                else {}
            ),
        },
        index=pd.Index([w.window.start.astimezone(tz).strftime("%d.%m %H:%M") for w in snap.windows], name="Pencere başı"),
    ).astype(float)
    if chart["Gerçekleşen iş (dk)"].isna().all():
        st.info("Bu vardiya veri kapsamı dışında; pencere verisi yok.")
    else:
        st.line_chart(chart)
        st.caption("Boş noktalar veri kapsamı dışındaki pencerelerdir (sıfır değildir).")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Kapasite (rol bazında)")
        if snap.capacity is None:
            st.caption(snap.capacity_total.note or "Kapasite verisi yok.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [{"Rol": role_label(r), "Kullanılabilir (dk)": round(m, 1)} for r, m in snap.capacity_by_role.items()]
                ),
                hide_index=True,
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Personel": row.synthetic_code, "Rol": role_label(row.role), "Çalışma (dk)": round(row.work_minutes, 1),
                            "Mola (dk)": round(row.break_minutes, 1), "İzin/diğer (dk)": round(row.other_unavailable_minutes, 1),
                            "Net (dk)": round(row.net_minutes, 1),
                            "Yetkinlik (seçilen an)": ", ".join(snap.staff_skills.get(row.staff_id, ())) or "—",
                            "Uyarı": "; ".join(row.warnings),
                        }  # fmt: skip
                        for row in snap.capacity.staff
                    ]
                ),
                hide_index=True,
            )
            st.caption("Yetkinlik: seçilen anda geçerli kayıtlar (salt okunur). \"—\" = bu anda geçerli yetkinlik kaydı yok.")
    with right:
        st.markdown("#### Bekleyen görevler (seçilen an)")
        if not snap.pending_tasks:
            st.caption(snap.pending.note or "Bekleyen görev yok.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "İşlem": t["name"], "Rol": role_label(t["role"]), "Son tarih": _local(t["due_at"], tz),
                            "Tahmini (dk)": round(t["estimated_minutes"], 1), "Durum": t["status_at"],
                            "Gerekli yetkinlik": ", ".join(t.get("required_skills") or ()) or "—",
                            "Bakım düzeyi": "—" if t.get("acuity_level") is None else str(t["acuity_level"]),
                            "Bakım katsayısı": "—" if t.get("acuity_multiplier") is None else f"{t['acuity_multiplier']:.2f}",
                        }  # fmt: skip
                        for t in snap.pending_tasks
                    ]
                ),
                hide_index=True,
            )
            st.caption(
                "Bakım düzeyi/katsayısı: hastanın seçilen anda bilinen son değerlendirmesi (salt okunur). "
                "Bakımda \"—\" = bu anda bilinen değerlendirme yok; gerekli yetkinlikte \"—\" = görev türü yetkinlik istemiyor."
            )

    with st.expander("Uygunsuzluk aralıkları (vardiyayla kesişen kayıtlar, salt okunur)"):
        if not snap.unavailability_rows:
            st.caption("Bu vardiyada kayıtlı uygunsuzluk yok.")
        else:
            codes = {row.staff_id: row.synthetic_code for row in snap.capacity.staff} if snap.capacity else {}
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Personel": codes.get(u["staff_id"], f"#{u['staff_id']}"), "Neden": u["reason"],
                            "Başlangıç": _local(u["starts_at"], tz), "Bitiş": _local(u["ends_at"], tz),
                        }  # fmt: skip
                        for u in snap.unavailability_rows
                    ]
                ),
                hide_index=True,
            )

    seed = snap.coverage.seed or {}
    st.caption(
        "Veri kaynağı: "
        + (
            f"{seed.get('database')} · seed {seed.get('seed')} · {seed.get('days')} gün · içerik özeti "
            f"{str(seed.get('content_sha256'))[:12]}… · üretici {seed.get('generator_version')}"
            if seed
            else snap.coverage.source
        )
        + f" · Hesap zamanı: {_local(snap.computed_at, tz)} · Model: bu panelde kullanılmıyor (bkz. Öneri inceleme)"
    )
    if snap.quality.completed_in_shift:
        st.caption(
            f"Simülasyon varsayımı: görevin gerçekleşmesi [tamamlanma − gerçekleşen süre, tamamlanma) aralığında "
            f"kabul edilir. Bu vardiyadaki {snap.quality.completed_in_shift} görevin "
            f"{snap.quality.realized_before_created} tanesinde türetilen başlangıç, görevin oluşturulmasından önceye "
            "düşüyor (sentetik veri sınırlaması)."
        )
    for warning in snap.quality.break_summary_warnings[:5]:
        st.caption(f"Veri tutarlılığı: {warning}")
