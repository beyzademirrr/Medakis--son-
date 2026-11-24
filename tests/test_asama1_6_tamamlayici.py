"""Aşama 1–6 tamamlayıcı işleri: son dört pencere referansı, salt okunur görünümler ve güvenli dışa aktarım.

Veritabanı yok; ağır senaryo yerine U4/U6 fikstürleriyle kurulan sahte senaryo kullanılır (test_review_asama6).
Model seçimi, U1 kalibrasyonu, CP-SAT ve onay güvenlik kuralları değiştirilmez; yalnız gösterim ve dışa aktarım sınanır.
"""

import copy
import csv
import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import test_review_asama6 as R6
from streamlit.testing.v1 import AppTest

from medakis.domain.planning import PlannedUnavailability
from medakis.forecasting.models import LAG_COLUMNS, LAST_FOUR_TARGETS, DEMAND_TARGETS, LastFourBaseline, SeasonalBaseline
from medakis.services import review_service as RV
from medakis.ui import review_panel, workload_panel

ROOT = Path(__file__).resolve().parents[1]
T, M = R6.T, R6.M


# ---- Son dört pencere referansı ------------------------------------------------------------------------------


def _frame(lags, **extra):
    data = {c: [v] for c, v in zip(LAG_COLUMNS, lags)}
    data.update({k: [v] for k, v in extra.items()})
    return pd.DataFrame(data)


def test_last_four_scales_mean_rate_to_horizon():
    pred = LastFourBaseline().predict(_frame([8.0, 10.0, 12.0, 10.0]))  # ortalama 10 dk / 15 dk
    assert pred.loc[0, "talep_dk_30"] == pytest.approx(20.0) and pred.loc[0, "talep_dk_60"] == pytest.approx(40.0)
    assert pred.loc[0, "gerceklesen_dk_30"] == pytest.approx(20.0) and pred.loc[0, "gerceklesen_dk_60"] == pytest.approx(40.0)


@pytest.mark.parametrize("lags", [[np.nan, 1, 1, 1], [1, np.inf, 1, 1]])
def test_last_four_no_prediction_with_insufficient_history(lags):
    pred = LastFourBaseline().predict(_frame(lags))
    assert pred.isna().all(axis=None)  # şartname: yetersiz geçmişte tahmin üretilmez


def test_last_four_reads_only_lag_inputs_and_is_reference_only():
    base = LastFourBaseline().predict(_frame([4, 4, 4, 4]))
    noisy = LastFourBaseline().predict(_frame([4, 4, 4, 4], talep_dk_30=999.0, gerceklesen_dk_60=-5.0, hasta_sayisi=50))
    pd.testing.assert_frame_equal(base, noisy)  # hedef ve diğer sütunlar okunmaz (sızıntı yok)
    assert set(LAST_FOUR_TARGETS) <= set(DEMAND_TARGETS) | {"gerceklesen_dk_30", "gerceklesen_dk_60"}
    assert not any("_nurse" in t or "_supervisor" in t or "_support" in t for t in LAST_FOUR_TARGETS)  # rol girdisi yok
    assert not hasattr(LastFourBaseline, "fit") and SeasonalBaseline().name == "baseline"  # öğrenme yok; mevcut baseline aynı


def test_last_four_evaluation_record_uses_same_test_split_and_unchanged_models():
    ref = json.loads((ROOT / "docs/veri/asama4_son_dort_baseline.json").read_text(encoding="utf-8"))
    main = json.loads((ROOT / "docs/veri/asama4_sonuclar.json").read_text(encoding="utf-8"))
    assert ref["secime_etkisi"] == "yok" and ref["kalibrasyona_etkisi"] == "yok" and ref["test_satir"] == 8052
    assert ref["veri"]["cerceve_sha256"] == main["veri"]["cerceve_sha256"] and main["veri"]["cerceve_sha256"].startswith("f35899f7")
    for target in ("talep_dk_30", "talep_dk_60"):
        for model in ("baseline", "random_forest"):
            new = next(r for r in ref["test"][target][model] if r["servis"] == "tum")["mae"]
            old = next(r for r in main["test"][model] if r["hedef"] == target and r["servis"] == "tum")["mae"]
            assert new == pytest.approx(old, abs=1e-9)  # mevcut modeller ve test dilimi değişmedi
        assert next(r for r in ref["test"][target]["son_dort_pencere"] if r["servis"] == "tum")["kapsama"] == 1.0


# ---- Salt okunur görünümler -----------------------------------------------------------------------------------


@pytest.fixture()
def svc():
    return R6.make_service()


def test_needs_table_mirrors_u3_rows(svc):
    run = svc.evaluate(T)
    rows = svc.needs_table(run)
    assert len(rows) == len(run.layer_a_inputs.u3_rows)
    first, src = rows[0], run.layer_a_inputs.u3_rows[0]
    assert first["tamponlu_talep_dk"] == src["tamponlu_talep_dk"] and first["durum"] == src["durum"]


def test_layer_b_details_show_verified_solution_only(svc):
    run = svc.evaluate(T)
    d = svc.layer_b_details(run)
    sol = run.layer_b["cozum"]
    assert [a["gorev"] for a in d["atamalar"]] == [a["gorev"] for a in sol["atamalar"]]
    assert sum(x["atanan_dk"] for x in d["kisi_yuku"]) == sum(a["sure_dk"] for a in sol["atamalar"])
    assert d["rezerv"] == sol["rezerv"] and d["atanmayanlar"] == sol["atanmayanlar"]
    assert d["cozucu_sure_siniri_sn"] == {"katman_a": run.layer_a_inputs.config.time_limit_s, "katman_b": run.layer_b_inputs.config.time_limit_s}
    unverified = replace(run, layer_b={**run.layer_b, "denetim_gosterilebilir": False})
    assert svc.layer_b_details(unverified) is None


def test_evaluation_summary_reads_records_and_reports_missing(tmp_path):
    s = RV.evaluation_summary(ROOT)
    assert s["eksik"] == [] and s["secim"]["secilen_rf"] == "rf_a_derin"
    assert {r["model"] for r in s["son_dort"]} >= {"son_dort_pencere", "random_forest"}
    empty = RV.evaluation_summary(tmp_path)
    assert len(empty["eksik"]) == 2 and empty["test"] == [] and empty["son_dort"] == []


# ---- Güvenli dışa aktarım -------------------------------------------------------------------------------------


def _doc(svc, pid):
    data, name, meta = svc.export_proposal(pid, "json")
    assert hashlib.sha256(data).hexdigest() == meta["dosya_sha256"] and name.endswith(".json")
    return json.loads(data), meta


def test_export_only_registered_proposals(svc):
    with pytest.raises(ValueError, match="oneri_bulunamadi"):
        svc.export_proposal("yok")
    _, pid, _ = R6.registered(svc)
    with pytest.raises(ValueError, match="bilinmeyen_bicim"):
        svc.export_proposal(pid, "xml")


def test_export_labels_pending_approved_stale_and_revoked(svc):
    _, pid, sha = R6.registered(svc)
    doc, meta = _doc(svc, pid)
    assert doc["etiket"] == "GECERLI_PLAN_DEGIL" and doc["gecerli_plan_mi"] is False and doc["plan_sha256"] == sha
    assert doc["plan_sha256_yeniden_hesaplanan"] == sha and "sistem hiçbir planı uygulamaz" in doc["notlar"][0]
    manager = svc.open_session("demo_yonetici")
    assert svc.decide("onayla", manager, pid, sha).ok
    doc, _ = _doc(svc, pid)
    assert doc["etiket"] == "GECERLI_ONAYLI_PLAN" and doc["gecerli_plan_mi"] is True and doc["gecerlilik"]["gecerli"]
    svc.scenario.later_absence = PlannedUnavailability(990, 11, T + 10 * M, T + 40 * M, "devamsizlik", T, "ani")
    doc, _ = _doc(svc, pid)
    assert doc["etiket"] == "ONAYLI_AMA_ARTIK_GECERSIZ" and doc["gecerli_plan_mi"] is False  # eski öneri geçerli plan değil
    assert any(c.startswith("girdi_degisti_yeniden_inceleme_gerekli") for c in doc["gecerlilik"]["nedenler"])
    svc.scenario.later_absence = None
    assert svc.decide("geri_al", manager, pid, sha, "yeniden inceleme").ok
    doc, _ = _doc(svc, pid)
    assert doc["etiket"] == "GECERLI_PLAN_DEGIL" and doc["durum"] == "geri_alindi"


def test_export_csv_carries_hash_and_label_on_every_row(svc):
    _, pid, sha = R6.registered(svc)
    data, name, meta = svc.export_proposal(pid, "csv")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    assert rows and all(r["plan_sha256"] == sha and r["etiket"] == "GECERLI_PLAN_DEGIL" and r["oneri_kimligi"] == pid for r in rows)
    assert name.endswith(".csv") and meta["dosya_sha256"] == hashlib.sha256(data).hexdigest()


def test_export_refused_when_trail_tampered(svc):
    _, pid, _ = R6.registered(svc)
    svc.approvals.journal._events[0]["aktor"] = "baskasi"
    with pytest.raises(ValueError, match="denetim_izi_bozuk"):
        svc.export_proposal(pid)


def test_export_refuses_experimental_snapshot_defensively(svc):
    _, pid, _ = R6.registered(svc)
    events = svc.approvals.journal._events
    tampered = copy.deepcopy(events)
    tampered[0]["anlik_goruntu"]["calisma_yapilandirmasi"]["dondurma_politikasi"] = "iki_yonlu"
    svc.approvals.journal._events = tampered  # zincir bozulur → dışa aktarım reddedilir (deneysel görüntü kayda giremez)
    with pytest.raises(ValueError):
        svc.export_proposal(pid)


# ---- Arayüz ---------------------------------------------------------------------------------------------------


def test_ui_shows_read_only_details_evaluation_and_export(monkeypatch):
    service = R6.make_service()
    monkeypatch.setenv("MEDAKIS_MODE", "demo")
    monkeypatch.setattr(workload_panel, "service_provider", R6._unavailable)
    monkeypatch.setattr(review_panel, "service_provider", lambda: service)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.button(key="senaryo_yukle").click().run()
    at.selectbox(key="aktor_secimi").select("demo_yonetici").run()
    at.button(key="oturum_ac").click().run()
    at.button(key="degerlendir").click().run()
    assert not at.exception, at.exception
    labels = [e.label for e in at.expander]
    assert "İhtiyaç–kapasite tablosu (U3, salt okunur)" in labels and "Katman B görev denetimi ayrıntıları (doğrulanmış, salt okunur)" in labels
    captions = " ".join(c.value for c in at.caption)
    assert "çözücü süre sınırı" in captions and "kullanıcı değiştiremez" in captions
    assert "Aşama 4 model değerlendirme özeti (salt okunur)" in " ".join(m.value for m in at.markdown)
    at.button(key="kaydet_a").click().run()
    assert not at.exception
    assert "Dışa aktarım etiketi: **GECERLI_PLAN_DEGIL**" in " ".join(c.value for c in at.caption)
