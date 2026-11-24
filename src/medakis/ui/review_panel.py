"""Aşama 6: öneri inceleme, insan onayı ve denetim geçmişi ekranı.

UI'da SQL, çözücü ya da kural hesabı yoktur; yalnız ReviewService çağrılır. Bütün kararlar (onay, ret, iptal, geri
alma) servis üzerinden U7 kontrollerinden geçer: yetki, güncellik ve SHA-256. Deneysel K1 sonuçları bu ekrana gelmez.
Sistem hiçbir planı uygulamaz; gerçek atama yapılmaz. Kimlik yerel prototip oturumudur, doğrulanmış kimlik değildir.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from medakis.approvals.plan_hash import KIND_A, KIND_B
from medakis.services.review_service import ReviewService, evaluation_summary

ROOT = Path(__file__).resolve().parents[3]

STATE_LABELS = {"beklemede": "Beklemede", "onaylandi": "Onaylandı", "reddedildi": "Reddedildi", "iptal_edildi": "İptal edildi",
                "geri_alindi": "Geri alındı"}  # fmt: skip
KIND_LABELS = {KIND_A: "Katman A kadro önerisi", KIND_B: "Katman B görev denetimi"}
ACTION_LABELS = {"onayla": "Onayla", "reddet": "Reddet", "iptal_et": "İptal et", "geri_al": "Onayı geri al"}
NOTICE = (
    "**Yalnız sentetik prototip ve insan incelemesi.** Sistem hiçbir planı uygulamaz ve gerçek personel ataması yapmaz. "
    "Onay, yalnız insan kararının izlenebilir kaydıdır. Kurallar ve yetkiler `test_sentetik` prototip değerleridir, "
    "yetkili karar değildir. Deneysel servis-rol dondurma (K1) sonuçları burada gösterilmez."
)
IDENTITY = "Kimlik yerel prototip oturumudur; doğrulanmış kurumsal kimlik değildir."


def _default_service() -> ReviewService:
    from medakis.approvals.audit_log import FileJournal
    from medakis.services.review_service import SimulationClock, demo_policies

    root = Path(__file__).resolve().parents[3]
    scenario = _scenario(str(root))
    moments = scenario.moments()
    journal = FileJournal(root / "data" / "onay_izi" / f"iz-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl")
    authz, policy = demo_policies(min(m["an"] for m in moments) - timedelta(days=30))
    return ReviewService(scenario, journal, authz, policy, SimulationClock(min(m["an"] for m in moments)))


@st.cache_resource(show_spinner=False)
def _scenario(root: str):  # noqa: ANN202
    from medakis.services.review_scenario import SyntheticReviewScenario

    return SyntheticReviewScenario(Path(root))


# Testler bu sağlayıcıyı değiştirerek ağır senaryo olmadan paneli çalıştırır.
service_provider: Callable[[], ReviewService] = _default_service


def _service() -> ReviewService | None:
    if "inceleme_servisi" not in st.session_state:
        # Tembel yükleme: sentetik senaryo ağırdır; uygulama her açıldığında değil, istenince yüklenir.
        st.caption("Senaryo: üretici 2.1, Aşama 4 RF ve U1 kalibrasyonu (yerel model dosyaları gerekir; veritabanı yok).")
        if not st.button("İnceleme senaryosunu yükle", key="senaryo_yukle"):
            return None
        try:
            with st.spinner("Sentetik senaryo yükleniyor (üretici 2.1, modeller, kalibrasyon; ilk açılış ~30 sn)…"):
                svc = service_provider()
                preflight = getattr(svc.scenario, "preflight", None)
                if preflight is not None:
                    preflight()  # eksik dosya / özet uyuşmazlığı burada yakalanır; sahte çıktı üretilmez
            st.session_state["inceleme_servisi"] = svc
        except Exception as exc:  # noqa: BLE001
            from medakis.services.review_scenario import RESOLUTION

            codes = getattr(exc, "codes", None) or [str(exc)]
            st.error("İnceleme senaryosu açılamadı: " + ", ".join(codes))
            st.markdown("**Çözüm:**")
            st.code(RESOLUTION, language="text")
            return None
    return st.session_state["inceleme_servisi"]


def _local(value: Any, svc: ReviewService) -> str:
    if value is None:
        return "—"
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    try:
        tz = svc.scenario.timezone
    except Exception:  # noqa: BLE001
        tz = None
    return moment.astimezone(tz).strftime("%d.%m.%Y %H:%M") if tz else moment.isoformat()


def _show_decision(result: Any) -> None:
    if result.ok:
        st.success("İşlem kaydedildi." + (f" ({', '.join(result.codes)})" if result.codes else ""))
    else:
        st.error("İşlem reddedildi: " + ", ".join(result.codes))


def _flash(result: Any, label: str) -> None:
    """Sonucu sakla ve yeniden çiz: durum, geçerlilik ve geçmiş güncel izden gösterilir."""
    st.session_state["inceleme_mesaji"] = (label, result)
    st.rerun()


def _show_flash() -> None:
    item = st.session_state.pop("inceleme_mesaji", None)
    if item is not None:
        label, result = item
        st.markdown(f"**Son işlem:** {label}")
        _show_decision(result)


def render() -> None:
    st.subheader("Öneri inceleme ve onay")
    st.info(NOTICE, icon="🛡️")
    svc = _service()
    if svc is None:
        return
    _show_flash()
    _session_box(svc)
    _clock_box(svc)
    st.divider()
    _evaluation_box(svc)
    st.divider()
    _proposals_box(svc)
    st.divider()
    _history_box(svc)
    st.divider()
    _model_evaluation_box()


def _session_box(svc: ReviewService) -> None:
    st.markdown("#### Oturum")
    session = st.session_state.get("inceleme_oturumu")
    cols = st.columns([3, 1])
    if session is None:
        actor = cols[0].selectbox("Aktör (prototip yetki politikası)", svc.actors(), key="aktor_secimi")
        if cols[1].button("Oturum aç", key="oturum_ac"):
            st.session_state["inceleme_oturumu"] = svc.open_session(actor)
            st.rerun()
    else:
        cols[0].write(f"Oturum: **{session.actor_id}** · {IDENTITY}")
        if cols[1].button("Oturumu kapat", key="oturum_kapat"):
            del st.session_state["inceleme_oturumu"]
            st.rerun()


def _clock_box(svc: ReviewService) -> None:
    cols = st.columns([3, 1, 1, 1])
    cols[0].write(f"Senaryo saati (sentetik yeniden oynatma; yalnız ileri gider): **{_local(svc.clock(), svc)}**")
    for col, minutes in zip(cols[1:], (5, 15, 60)):
        if col.button(f"+{minutes} dk", key=f"saat_{minutes}"):
            svc.clock.advance(minutes)
            st.rerun()


def _evaluation_box(svc: ReviewService) -> None:
    st.markdown("#### Karar anı değerlendirmesi")
    moments = svc.scenario.moments()
    labels = {f"{m['yerel']} · Katman A (varsayılan): {m['katman_a_varsayilan']}": m["an"] for m in moments}
    choice = st.selectbox("Karar anı (Aşama 5 değerlendirme örneklemi)", list(labels), key="an_secimi")
    if st.button("Değerlendir", key="degerlendir"):
        at = labels[choice]
        if svc.clock() > at:
            st.warning("Senaryo saati bu karar anını geçti; kayıt ve onay süre/güncellik kontrollerinden geçmeyebilir.")
        svc.clock.at_least(at)
        with st.spinner("U3 → U4 (varsayılan politika) → U6 çalışıyor…"):
            st.session_state["inceleme_calismasi"] = svc.evaluate(at)
        st.rerun()
    run = st.session_state.get("inceleme_calismasi")
    if run is None:
        st.caption("Bir karar anı seçip değerlendirin.")
        return
    session = st.session_state.get("inceleme_oturumu")
    a, b = run.layer_a, run.layer_b
    left, right = st.columns(2)
    with left:
        st.markdown(f"**Katman A kadro önerisi** · durum `{a['durum']}`")
        if a["oneri_yayinlanabilir"]:
            st.caption(f"Plan SHA-256: `{run.plan_sha_a}` · doğrulayıcı: geçti · insan onayı zorunlu")
            st.dataframe(pd.DataFrame([{"personel": m["personel"], "rol": m["rol"], "kaynak": m["son_onayli_servis"], "hedef": m["hedef_servis"],
                                        "bitis": _local(m["bitis"], svc)} for m in a["tasimalar"]] or [{"bilgi": "taşıma yok (mevcut yerleşim)"}]),
                         hide_index=True, width="stretch")  # fmt: skip
            if st.button("Katman A önerisini incelemeye kaydet", key="kaydet_a", disabled=session is None):
                _flash(svc.register(session, run, KIND_A), "Katman A kaydı")
        else:
            st.warning("Yayımlanabilir öneri yok (güvenli ret). Nedenler: " + ", ".join(a["nedenler"][:8] or ["—"]))
    with right:
        st.markdown(f"**Katman B görev denetimi** · durum `{b['durum']}`")
        if b["denetim_gosterilebilir"]:
            sol = b["cozum"]
            k = sol["kapsam"]
            st.caption(f"Plan SHA-256: `{run.plan_sha_b}` · doğrulayıcı: geçti · gerçek atama yok")
            c1, c2, c3 = st.columns(3)
            c1.metric("Kapsamdaki görev", k["pencerede_son_tarihli_gorev"])
            c2.metric("Atanabilir", len(sol["atamalar"]))
            c3.metric("Atanmayan", len(sol["atanmayanlar"]))
            reasons = pd.Series([g["neden"] for g in sol["atanmayanlar"]], dtype="object").value_counts()
            if len(reasons):
                st.dataframe(reasons.rename("görev").to_frame(), width="stretch")
            if st.button("Katman B denetimini incelemeye kaydet", key="kaydet_b", disabled=session is None):
                _flash(svc.register(session, run, KIND_B), "Katman B kaydı")
        else:
            st.warning("Denetim gösterilemez. Nedenler: " + ", ".join(b["nedenler"][:8] or ["—"]))
    if session is None:
        st.caption("Kayıt için oturum açın.")
    _run_details(svc, run)


def _dash(value: Any) -> str:
    return "—" if value is None or value == [] or value == "" else str(value)  # karışık tür sütunu oluşmaz


def _people_and_tasks(svc: ReviewService, run: Any) -> None:
    """Karar anında bilinen yetkinlik, uygunsuzluk aralıkları ve bakım gereksinimi (senaryo verisi; salt okunur)."""
    view = svc.people_and_tasks(run)
    window = view["pencere"]
    with st.expander("Personel ve görev ayrıntıları (karar anında bilinen, salt okunur)"):
        st.caption(f"Pencere {_local(window.start, svc)} – {_local(window.end, svc)} (çözücülerle aynı). Sonradan bildirilen "
                   "kayıt görünmez. \"—\" = karar anında kayıt yok; değer uydurulmaz.")  # fmt: skip
        tab_staff, tab_unav, tab_tasks = st.tabs(["Personel yetkinlikleri", "Uygunsuzluk aralıkları", "Bekleyen görevler"])
        with tab_staff:
            if view["personel"]:
                st.dataframe(pd.DataFrame([{"Personel": r["personel"], "Rol": r["rol"], "Servis": ", ".join(map(str, r["servisler"])),
                                            "Yetkinlik": _dash(", ".join(r["yetkinlikler"]))} for r in view["personel"]]),
                             hide_index=True, width="stretch")  # fmt: skip
            else:
                st.caption("Pencerede planlı vardiyası olan personel yok.")
        with tab_unav:
            if view["uygunsuzluk"]:
                st.dataframe(pd.DataFrame([{"Personel": u["personel"], "Neden": u["neden"], "Tür": u["tur"],
                                            "Başlangıç": _local(u["baslangic"], svc), "Bitiş": _local(u["bitis"], svc),
                                            "Bildirildi": _local(u["bildirildi"], svc)} for u in view["uygunsuzluk"]]),
                             hide_index=True, width="stretch")  # fmt: skip
            else:
                st.caption("Pencereyle kesişen, karar anında bilinen uygunsuzluk kaydı yok.")
        with tab_tasks:
            if view["gorevler"]:
                st.dataframe(pd.DataFrame([{"Görev": t["gorev"], "Servis": t["servis"], "Rol": t["rol"], "Tür": t["gorev_turu"],
                                            "Tahmini (dk)": t["tahmini_dk"], "Son tarih": _local(t["son_tarih"], svc),
                                            "Gerekli yetkinlik": _dash(", ".join(t["gerekli_yetkinlik"])),
                                            "Bakım düzeyi": _dash(t["bakim_duzeyi"]), "Bakım katsayısı": _dash(t["bakim_katsayisi"])}
                                           for t in view["gorevler"]]), hide_index=True, width="stretch")  # fmt: skip
            else:
                st.caption("Karar anında bilinen bekleyen görev yok.")
            if view["belirsiz_gorevler"]:
                st.caption(f"İptal zamanı bilinmeyen {len(view['belirsiz_gorevler'])} görev listelenmez (belirsiz).")
        for note in view["notlar"]:
            st.caption(note)


def _run_details(svc: ReviewService, run: Any) -> None:
    """Salt okunur ayrıntılar: yeni hesap yok; yalnız U3 satırları ve doğrulanmış Katman B çözümü gösterilir."""
    with st.expander("İhtiyaç–kapasite tablosu (U3, salt okunur)"):
        st.caption("D = ŷ + B (tamponlu talep, iş-dk). Kapasite karar anında bilinen planlı kadrodan. Durum ve nedenler güvenli "
                   "ret kararının dayanağıdır.")  # fmt: skip
        st.dataframe(pd.DataFrame(svc.needs_table(run)), hide_index=True, width="stretch")
    _people_and_tasks(svc, run)
    details = svc.layer_b_details(run)
    with st.expander("Katman B görev denetimi ayrıntıları (doğrulanmış, salt okunur)"):
        if details is None:
            st.caption("Doğrulanmış Katman B sonucu yok; ayrıntı gösterilmez.")
            return
        limits = details["cozucu_sure_siniri_sn"]
        st.caption(f"Durum {details['durum']} · sözlük sırası tam: {details['sozluk_sirali_tam']} · çözücü süre sınırı (aşama başına, "
                   f"yapılandırmadan; kullanıcı değiştiremez): Katman A {limits['katman_a']} sn, Katman B {limits['katman_b']} sn · "
                   f"O1 atanmayan {details['amac_degerleri']['O1_atanmayan_gorev_dk']} dk, O2 yük farkı "
                   f"{details['amac_degerleri']['O2_yuk_farki_dk']} dk. Gerçek atama değildir.")  # fmt: skip
        tabs = st.tabs(["Görev atamaları", "Kişi bazında yük", "Atanamayan işler", "Tahmin rezervi"])
        frames = [details["atamalar"], details["kisi_yuku"], details["atanmayanlar"], details["rezerv"]]
        for tab, rows in zip(tabs, frames):
            with tab:
                if rows:
                    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
                else:
                    st.caption("Kayıt yok.")
        st.caption("Tahmin rezervi kimliksiz talebi gösterir; görev gibi atanmaz ve atanamayan somut işlerle toplanmaz.")


def _model_evaluation_box() -> None:
    st.markdown("#### Aşama 4 model değerlendirme özeti (salt okunur)")
    summary = evaluation_summary(ROOT)
    if summary["eksik"]:
        st.warning("Değerlendirme dosyası bulunamadı: " + ", ".join(summary["eksik"]))
    if summary["secim"]:
        st.caption(f"Model seçimi yalnız doğrulama bölümüyle: seçilen RF `{summary['secim']['secilen_rf']}`. Test bölümü seçimde "
                   "kullanılmadı; çok çıktılı RF korunuyor (karar kaydı).")  # fmt: skip
    if summary["test"]:
        st.dataframe(pd.DataFrame(summary["test"]), hide_index=True, width="stretch")
    if summary["son_dort"]:
        st.markdown("**Şartnamedeki son dört pencere baseline'ı (yalnız karşılaştırma referansı)**")
        st.caption("Aynı test dilimi. Seçime ve U1 kalibrasyonuna girmez. Rol hedeflerinde tanımsız (rol gecikme girdisi yok).")
        st.dataframe(pd.DataFrame(summary["son_dort"]), hide_index=True, width="stretch")


def _proposals_box(svc: ReviewService) -> None:
    st.markdown("#### Kayıtlı öneriler")
    rows = svc.proposals()
    if not rows:
        st.caption("Henüz kayıtlı öneri yok.")
        return
    table = pd.DataFrame([{"kimlik": r["kimlik"], "tür": KIND_LABELS[r["tur"]], "karar anı": _local(r["karar_ani"], svc),
                           "durum": STATE_LABELS[r["durum"]], "plan SHA-256": r["plan_sha256"][:16] + "…", "kalem": r["kalem"],
                           "son karar": r["son_karar_aktor"] or "—"} for r in rows])  # fmt: skip
    st.dataframe(table, hide_index=True, width="stretch")
    pid = st.selectbox("İncelenecek öneri", [r["kimlik"] for r in rows], key="oneri_secimi")
    prop = svc.proposal(pid)
    snap = prop["anlik_goruntu"]
    st.markdown(f"**{KIND_LABELS[snap['tur']]}** · {STATE_LABELS[prop['durum']]} · karar anı {_local(snap['karar_ani'], svc)}")
    st.code(f"plan_sha256 = {prop['plan_sha256']}\ncozum_ozeti = {snap['cozum_surumu']['cozum_ozeti']}\n"
            f"cozucu      = {snap['cozum_surumu']['cozucu']}\nmodeller    = "
            f"{', '.join(m['model_surumu'] or '—' for m in snap['model_surumleri']) or '—'}", language="text")  # fmt: skip
    st.dataframe(pd.DataFrame(snap["kalemler"] or [{"bilgi": "kalem yok"}]), hide_index=True, width="stretch")
    ok, codes = svc.applicability(pid) if prop["durum"] == "onaylandi" else (False, [])
    if prop["durum"] == "onaylandi":
        (st.success if ok else st.warning)("Onay şu an geçerli." if ok else "Onay geçerli değil: " + ", ".join(codes))
    session = st.session_state.get("inceleme_oturumu")
    with st.form(f"karar_{pid}"):
        action = st.radio("Karar", list(ACTION_LABELS), format_func=ACTION_LABELS.get, horizontal=True, key=f"eylem_{pid}")
        text = st.text_input("Gerekçe / not (ret, iptal ve geri almada zorunlu)", key=f"gerekce_{pid}")
        st.caption(f"Bu karar ekranda gösterilen plan özetine bağlanır: `{prop['plan_sha256'][:16]}…`. Sistem planı uygulamaz.")
        submitted = st.form_submit_button("Kararı kaydet", disabled=session is None, key=f"karar_gonder_{pid}")
    if submitted:
        _flash(svc.decide(action, session, pid, prop["plan_sha256"], text), f"{ACTION_LABELS[action]} · {pid}")
    if session is None:
        st.caption("Karar için oturum açın.")
    _export_box(svc, pid)
    if prop["durum"] == "onaylandi" and ok:
        _notification_box(pid, prop)


def _notifier():  # noqa: ANN202
    from medakis.approvals.audit_log import FileJournal
    from medakis.notifications.telegram import TelegramNotifier

    if "bildirim_servisi" not in st.session_state:
        root = Path(__file__).resolve().parents[3]
        path = root / "data" / "bildirim_izi" / f"bildirim-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
        st.session_state["bildirim_servisi"] = TelegramNotifier(FileJournal(path))
    return st.session_state["bildirim_servisi"]


def _notification_box(pid: str, prop: dict) -> None:
    """Yalnız geçerli onaylı öneri için. Varsayılan dry-run; gerçek gönderim ortam yapılandırması ister."""
    from medakis.notifications.telegram import LIVE

    notifier = _notifier()
    live = notifier.config.live_ready
    st.markdown("#### Onay bildirimi (Telegram)")
    st.caption(("Kip: **CANLI** — yalnız yapılandırılmış tek demo sohbetine gönderilir; personelin kişisel hesaplarına "
                "gönderilmez." if live else "Kip: **DRY-RUN** (varsayılan) — ağ isteği yapılmaz; hazırlanan mesaj ve sonuç "
                "bildirim izine yazılır.") + (" Canlı kip istendi ama token/chat ID eksik." if notifier.config.mode == LIVE and not live else ""))  # fmt: skip
    if st.button("Bildirimi hazırla ve gönder" if live else "Bildirimi hazırla (dry-run)", key=f"bildirim_{pid}"):
        r = notifier.notify_approved({**prop, "id": pid}, prop["plan_sha256"])
        msg = f"{r.status}: {r.detail}"
        {"gonderildi": st.success, "dry_run": st.info}.get(r.status, st.warning)(msg)
        if r.message:
            st.code(r.message, language="text")
    events = notifier.journal.events()
    if events:
        st.dataframe(pd.DataFrame([{k: e.get(k) for k in ("sira", "zaman", "kip", "durum", "oneri_id", "ayrinti")} for e in events]),
                     hide_index=True, width="stretch")  # fmt: skip


def _export_box(svc: ReviewService, pid: str) -> None:
    """Yalnız kayıtlı öneri; dosyada plan SHA-256, durum, geçerlilik ve açık etiket bulunur."""
    try:
        data_json, name_json, meta = svc.export_proposal(pid, "json")
        data_csv, name_csv, meta_csv = svc.export_proposal(pid, "csv")
    except ValueError as exc:
        st.error(f"Dışa aktarım yapılamaz: {exc}")
        return
    st.caption(f"Dışa aktarım etiketi: **{meta['etiket']}** · JSON SHA-256 `{meta['dosya_sha256'][:16]}…` · CSV SHA-256 "
               f"`{meta_csv['dosya_sha256'][:16]}…`. Yalnız GECERLI_ONAYLI_PLAN etiketli dosya geçerli onaylı plandır.")  # fmt: skip
    cols = st.columns(2)
    cols[0].download_button("JSON indir", data_json, file_name=name_json, mime="application/json", key=f"indir_json_{pid}")
    cols[1].download_button("CSV indir", data_csv, file_name=name_csv, mime="text/csv", key=f"indir_csv_{pid}")


def _history_box(svc: ReviewService) -> None:
    st.markdown("#### Denetim geçmişi")
    rows, issues = svc.history()
    if issues:
        st.error("Denetim izi bütünlüğü bozuk: " + ", ".join(issues[:5]))
    elif rows:
        st.caption(f"Özet zinciri doğrulandı ({len(rows)} olay). Tek yazıcılı yerel prototip izi.")
    if rows:
        table = pd.DataFrame(rows)
        table["zaman"] = [_local(z, svc) for z in table["zaman"]]
        st.dataframe(table, hide_index=True, width="stretch")
    else:
        st.caption("Henüz olay yok.")
