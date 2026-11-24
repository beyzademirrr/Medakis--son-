"""MedAkis asistanı: kural tabanlı (deterministik) araç yönlendirmesi.

Bu asistan bir dil modeli DEĞİLDİR. Kullanıcı mesajındaki niyeti anahtar kelimelerle belirler, `agent.tools`
araçlarını çağırır ve sonucu araç çıktılarından oluşturur; serbest bilgi uydurmaz. Her yanıtla birlikte çağrılan araçlar
listelenir. Onay aracı yoktur: öneriyi yalnız yönetici onaylar. MCP entegrasyonu bu sürümde yoktur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from medakis.agent import tools as T
from medakis.density import staffing as S
from medakis.density.simulator import Simulator


@dataclass
class Reply:
    text: str
    tools: list[str] = field(default_factory=list)
    proposal: dict | None = None


def _density_text(sim: Simulator) -> tuple[str, list[dict]]:
    rows = T.get_service_density(sim)["alanlar"]
    lines = [f"- **{r['ad']}**: {r['kisi']}/{r['kapasite']} kişi (%{round(100 * r['doluluk'])}, {r['seviye']}), "
             f"aktif personel {r['aktif_personel']} / gereken {r['gereken_personel']}, personel başına {r['personel_basina_gorev']} bekleyen görev"
             for r in rows]  # fmt: skip
    return "\n".join(lines), rows


def _has(m: str, *prefixes: str) -> bool:
    """Sözcük BAŞINDA eşleşme (ör. 'plan' → 'planı' eşleşir, 'hesaplanır' eşleşmez)."""
    return any(re.search(r"(?<!\w)" + re.escape(p), m) for p in prefixes)


def answer(message: str, sim: Simulator, svc: T.TransferService) -> Reply:
    m = message.replace("İ", "i").replace("I", "ı").lower()
    area = T.area_key(m)
    if _has(m, "onay"):
        st = T.get_approval_status(svc)
        listing = "\n".join(f"- {p['id']} → {p['hedef']}: {p['durum']}" for p in st) or "Kayıtlı öneri yok."
        return Reply("Öneri durumları:\n" + listing, ["get_approval_status"])
    if _has(m, "yoğun", "yogun", "durum", "doluluk", "kalabalık") and not _has(m, "öner", "oner", "plan", "destek"):
        text, rows = _density_text(sim)
        crit = [r for r in rows if r["seviye"] == "kritik"]
        why = ""
        if crit:
            r = crit[0]
            why = (f"\n\n**{r['ad']}** kritik: doluluk %{round(100 * r['doluluk'])}, personel başına {r['personel_basina_gorev']} bekleyen görev. "
                   f"Kural: personel desteği yalnız kritik doluluk + iş yükü eşiği + personel açığı birlikte sağlanırsa önerilir.")  # fmt: skip
        return Reply("Sentetik kamera simülasyonuna göre güncel alan durumu:\n" + text + why, ["get_service_density"])
    if _has(m, "risk"):
        plan = T.optimize_staff_allocation(sim)
        if not plan.get("aktarimlar"):
            return Reply(f"Aktarım planı yok: {plan['gerekce']}", ["optimize_staff_allocation"])
        srcs = sorted({S.AREA_NAMES[a["source"]] for a in plan["aktarimlar"]})
        return Reply("Önerilen aktarımın riskleri:\n"
                     f"- Kaynak alan(lar) ({', '.join(srcs)}) asgari personel sınırına yaklaşır; yeni yoğunlukta geri çağırma gerekebilir.\n"
                     "- Aktarılan personel hedef alanın süreçlerine uyum süresi ister.\n"
                     "- Sentetik simülasyon verisidir; gerçek hastanede doğrulanmamıştır.\n"
                     "- Kontrol: doğrulayıcı rol, yetkinlik, vardiya, aktif görev ve asgari kapasiteyi yeniden denetledi "
                     f"({'ihlal yok' if not plan['dogrulama'] else ', '.join(plan['dogrulama'])}).", ["optimize_staff_allocation"])  # fmt: skip
    pinned = tuple(re.findall(r"\bp-\d{2}\b", m))
    if pinned or _has(m, "öner", "oner", "destek", "plan", "yönlendir", "yonlendir", "görevlendir"):
        target = area if (pinned or _has(m, "yönlendir", "yonlendir")) else None
        plan = T.optimize_staff_allocation(sim, target=target, pinned=tuple(p.upper() for p in pinned))
        called = ["get_service_density", "get_staff_availability", "optimize_staff_allocation"]
        if not plan.get("aktarimlar"):
            return Reply(f"Uygun bir aktarım planı oluşturulamadı: {plan['gerekce']}", called)
        if plan["dogrulama"]:
            return Reply("Plan bağımsız doğrulamadan geçmedi; öneri oluşturulmadı: " + ", ".join(plan["dogrulama"]), called)
        res = T.create_transfer_proposal(svc, plan)
        called.append("create_transfer_proposal")
        moves = "\n".join(f"- **{a['staff_id']}**: {S.AREA_NAMES[a['source']]} → {S.AREA_NAMES[a['target']]}" for a in plan["aktarimlar"])
        prop = res.get("oneri")
        return Reply(f"Önerilen geçici görevlendirme (CP-SAT, doğrulayıcı: ihlal yok):\n{moves}\n\n**Gerekçe:** {plan['gerekce']}\n\n"
                     f"Öneri **{prop['id']}** oluşturuldu ve **yönetici onayı bekliyor**. Onay 'Öneriler ve Onaylar' ekranındadır; "
                     "asistan onay veremez.", called, prop)  # fmt: skip
    if "onay" in m:
        st = T.get_approval_status(svc)
        return Reply("Öneri durumları:\n" + ("\n".join(f"- {p['id']} → {p['hedef']}: {p['durum']}" for p in st) or "Kayıtlı öneri yok."),
                     ["get_approval_status"])  # fmt: skip
    if "tahmin" in m:
        fc = T.get_workload_forecast(sim)
        last = fc["adimlar"][-1]
        return Reply("Simülasyon projeksiyonu (6 adım sonra; makine öğrenmesi tahmini değildir): " +
                     ", ".join(f"{S.AREA_NAMES[k]} {v}" for k, v in last.items()), ["get_workload_forecast"])  # fmt: skip
    ev = T.retrieve_policy_evidence(message)
    if not ev["bulundu"]:
        return Reply(ev["mesaj"] + " Desteklenen istekler: yoğunluk durumu, personel önerisi, risk, onay durumu, tahmin, kaynak belgede arama.",
                     ["retrieve_policy_evidence"])  # fmt: skip
    k = ev["kaynaklar"][0]
    return Reply(f"Kaynak belgede bulunan alıntı ({ev['yontem']}):\n\n> {k['alinti']}\n\n— {k['belge']} ({k['kimlik']} sürüm {k['surum']}, bölüm: {k['bolum']})",
                 ["retrieve_policy_evidence"])  # fmt: skip
