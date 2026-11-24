"""U7: Oturum kimliği ve işlem yetkisi. Varsayılan yetki YOK; kimlik yalnız UI oturumundan gelir.

Şartname Bölüm 11: kimlik gerçek UI oturumundan alınır, RAG metninden ya da belgeden alınmaz. Yerel prototipte
aktör adı doğrulanmış kurumsal kimlik değildir; üretim kimlik doğrulaması kapsam dışıdır. Yetki politikası kaynaklı
ve onaylı bir yapılandırma girdisidir; kimin neyi onaylayabileceği burada uydurulmaz.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

ACT_REGISTER, ACT_APPROVE, ACT_REJECT, ACT_CANCEL, ACT_REVOKE = "kaydet", "onayla", "reddet", "iptal_et", "geri_al"
ACTIONS = (ACT_REGISTER, ACT_APPROVE, ACT_REJECT, ACT_CANCEL, ACT_REVOKE)
UI_SESSION = "ui_oturumu"
IDENTITY_NOTE = "yerel_prototip_kimligi_dogrulanmis_kurumsal_kimlik_degil"


@dataclass(frozen=True)
class ActorSession:
    """UI oturumundan gelen aktör. `origin` yalnız "ui_oturumu" olabilir (RAG metni, belge veya serbest metin değil)."""

    actor_id: str
    session_id: str
    origin: str
    started_at: datetime


@dataclass(frozen=True)
class AuthorizationPolicy:
    """Aktör → izin verilen (işlem, servis) çiftleri. Kaynak, onaylayan ve bilinme anı zorunlu."""

    grants: Mapping[str, frozenset[tuple[str, int]]]
    source: str
    approved_by: str
    known_at: datetime

    def problems(self) -> list[str]:
        out = []
        if not self.source.strip() or not self.approved_by.strip():
            out.append("yetki_politikasi_kaynaksiz")
        for actor, grants in self.grants.items():
            if not str(actor).strip():
                out.append("yetki_politikasi_bos_aktor")
            out += [f"yetki_politikasi_bilinmeyen_islem:{a}" for a, _ in grants if a not in ACTIONS]
        return sorted(set(out))


def session_problems(session: object, now: datetime) -> list[str]:
    """Oturum doğrulaması: tür, köken, boş kimlik, gelecekte başlayan oturum."""
    if not isinstance(session, ActorSession):
        return ["oturum_yok_kimlik_metinden_alinamaz"]
    out = []
    if session.origin != UI_SESSION:
        out.append(f"oturum_kokeni_gecersiz:{session.origin}")
    if not session.actor_id.strip() or not session.session_id.strip():
        out.append("oturum_kimligi_bos")
    if session.started_at > now:
        out.append("oturum_gelecekte_basliyor")
    return out


def authorize(policy: AuthorizationPolicy | None, session: object, action: str, units: Iterable[int], now: datetime) -> list[str]:
    """Boş liste: yetkili. Aksi hâlde ret nedenleri. Bütün kapsam servisleri için izin gerekir."""
    out = session_problems(session, now)
    if policy is None:
        return out + ["yetki_politikasi_yok"]
    out += policy.problems()
    if policy.known_at > now:
        out.append("yetki_politikasi_islem_aninda_bilinmiyor")
    if action not in ACTIONS:
        out.append(f"bilinmeyen_islem:{action}")
    if isinstance(session, ActorSession):
        grants = policy.grants.get(session.actor_id, frozenset())
        missing = [u for u in sorted(set(units)) if (action, u) not in grants]
        out += [f"yetkisiz:{action}:{u}" for u in missing]
    return out
