"""MedAkis Telegram agent'ı: web ekranındaki agent ile aynı araçlar ve aynı kurallar, Telegram sohbeti üzerinden.

Güvenlik:
- Token ve sohbet kimliği yalnız ortam değişkeni ya da Windows Credential Manager'dan okunur (`medakis-telegram`); depoya,
  loga ya da mesaja yazılmaz. Hata metinleri `redact` ile temizlenir.
- Yalnız yapılandırılmış TEK sohbet kimliğinden gelen mesajlar yanıtlanır; diğerleri içerikleri okunmadan yok sayılır.
- Agent'ın yetkisi web ekranıyla aynıdır: veri okur, TASLAK öneri ve TASLAK talep açar. Onay, gerçek atama ve personel
  bildirimi yapamaz. Bot'un yazdığı yanıt, sohbet sahibine verilen agent yanıtıdır; personel emri değildir.
- Bot kendi oturumunu açar; kayıtlar `data/izler` altında özet zincirli izlere yazılır (web oturumundan ayrıdır).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from medakis.agent import llm
from medakis.agent import tools as T
from medakis.agent.orchestrator import answer, help_text
from medakis.notifications.telegram import API, TelegramConfig, redact
from medakis.twin.hub import Engine, Session

DEFAULT_MOMENT = datetime.fromisoformat("2027-01-19T06:30:00+00:00")
SCENARIOS = {"normal": {}, "acil": {3: 1.0}, "acil_kritik": {3: 1.5}, "poliklinik_dusus": {4: -0.4}}
KEYBOARD = [["Şu an hangi alanlarda yoğunluk var?"], ["Acil Servis için taslak emirleri oluştur"],
            ["Bugün hangi birimlerde personel açığı var?"], ["Onay bekleyen kararlar neler?", "Taleplerin durumu ne?"], ["yardım"]]  # fmt: skip
MAX_LEN = 3900


def plain(text: str) -> str:
    """Telegram düz metin: markdown kalın/alıntı işaretlerini kaldırır."""
    text = text.replace("**", "")
    return re.sub(r"^> ", "“", text, flags=re.M)


def chunks(text: str, size: int = MAX_LEN) -> list[str]:
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > size and buf:
            out.append(buf)
            buf = ""
        buf += (("\n" if buf else "") + line)[:size]
    return [*out, buf] if buf else out


@dataclass
class BotState:
    surges: dict[int, float] = field(default_factory=dict)
    minute: int = 20
    use_llm: bool = False


class TelegramAgent:
    def __init__(self, engine: Engine, config: TelegramConfig, http: Any = None, persist: bool = True,
                 log: Callable[[str], None] = print) -> None:  # fmt: skip
        if not (config.token and config.chat_id):
            raise ValueError("telegram_kimligi_eksik")
        self.engine, self.config, self.log = engine, config, log
        self.session = Session.open(engine, persist=persist)
        self.session.sync_clock(DEFAULT_MOMENT, 0)
        self.state = BotState()
        self.offset: int | None = None
        self.ignored = 0
        if http is None:
            import httpx

            http = httpx.Client(timeout=40.0)
        self.http = http

    # ---- Telegram API ---------------------------------------------------------------------------------------------

    def _api(self, method: str, **payload: Any) -> dict:
        resp = self.http.post(f"{API}/bot{self.config.token}/{method}", json=payload)
        data = resp.json() if resp is not None else {}
        if not data.get("ok"):
            raise RuntimeError(redact(f"telegram_{method}: {data.get('description', resp.status_code)}"))
        return data

    def send(self, text: str, keyboard: bool = True) -> None:
        markup = {"keyboard": [[{"text": b} for b in row] for row in KEYBOARD], "resize_keyboard": True, "is_persistent": True}
        for part in chunks(plain(text)):
            self._api("sendMessage", chat_id=self.config.chat_id, text=part, **({"reply_markup": markup} if keyboard else {}))

    def skip_backlog(self) -> None:
        """Bot kapalıyken birikmiş eski mesajları yanıtlamaz; yalnız bundan sonra gelenleri işler."""
        res = self._api("getUpdates", offset=-1, timeout=0)["result"]
        self.offset = res[-1]["update_id"] + 1 if res else None

    def poll_once(self, timeout: int = 25) -> int:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if self.offset is not None:
            payload["offset"] = self.offset
        handled = 0
        for upd in self._api("getUpdates", **payload)["result"]:
            self.offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            if str((msg.get("chat") or {}).get("id")) != str(self.config.chat_id):
                self.ignored += 1  # yetkisiz sohbet: içerik okunmaz, yanıt verilmez
                continue
            text = (msg.get("text") or "").strip()
            if text:
                self.send(self.handle(text))
                handled += 1
        return handled

    # ---- Mesaj işleme ---------------------------------------------------------------------------------------------

    def ctx(self) -> T.ToolContext:
        s = self.session
        return T.ToolContext(self.engine.twin, DEFAULT_MOMENT, self.state.minute, dict(self.state.surges),
                             create_proposal=lambda o: s.create_proposal(o, "telegram_agent"), create_request=s.create_request,
                             approval_status=s.proposals, request_status=s.request_list)  # fmt: skip

    def handle(self, text: str) -> str:
        t0 = time.perf_counter()
        low = text.lower()
        if low in ("/start", "/yardim", "/help"):
            return "MedAkis agent'ına hoş geldiniz (sentetik prototip, gerçek hasta/personel yok).\n\n" + help_text() + COMMANDS
        if low.startswith("/senaryo"):
            name = low.removeprefix("/senaryo").strip()
            if name not in SCENARIOS:
                return "Senaryolar: " + ", ".join(SCENARIOS) + "\nÖrnek: /senaryo acil_kritik"
            self.state.surges = dict(SCENARIOS[name])
            self.session.log("telegram_senaryo", ad=name)
            return f"Senaryo: {name}. Karar anı {self.engine.data.local(DEFAULT_MOMENT):%d.%m.%Y %H:%M} (+{self.state.minute} dk)."
        if low.startswith("/gemma"):
            st = llm.status()
            self.state.use_llm = st.available and not self.state.use_llm
            return f"Gemma yeniden ifade: {'açık' if self.state.use_llm else 'kapalı'} ({st.detail})."
        reply = answer(text, self.ctx())
        out, engine = reply.text, reply.engine
        if self.state.use_llm:
            new, why = llm.rephrase(reply.text)
            out, engine = (new, "Gemma 3 4B (sayı denetimi geçti)") if new else (out, f"Deterministik (Gemma kullanılmadı: {why})")
        self.session.log("telegram_agent_cagri", soru=text[:300], araclar=[c["arac"] for c in reply.calls],
                         oneri=(reply.proposal or {}).get("oneri"), motor=engine, sure_ms=round((time.perf_counter() - t0) * 1000))  # fmt: skip
        tools = ", ".join(c["arac"] for c in reply.calls) or "-"
        return f"{out}\n\n— Araçlar: {tools} · {engine}"

    def run(self, stop: Callable[[], bool] = lambda: False) -> None:
        for attempt in range(1, 7):
            try:
                self.skip_backlog()
                break
            except Exception as exc:  # noqa: BLE001 — ağ kesintisi: bekleyip yeniden dener
                self.log(f"Bağlantı kurulamadı ({attempt}/6): {redact(str(exc))[:160]}")
                time.sleep(5 * attempt)
        else:
            self.log("Telegram'a bağlanılamadı; bot durdu.")
            return
        self.log("MedAkis Telegram agent'ı çalışıyor (yalnız yapılandırılmış sohbet yanıtlanır). Durdurmak için Ctrl+C.")
        while not stop():
            try:
                n = self.poll_once()
                if n:
                    self.log(f"{n} mesaj yanıtlandı.")
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 — ağ kesintisi: kısa bekleyip devam
                self.log(f"Geçici hata: {redact(str(exc))[:200]}")
                time.sleep(5)


COMMANDS = ("\n\nKomutlar:\n/senaryo normal | acil | acil_kritik | poliklinik_dusus — yoğunluk senaryosu\n"
            "/gemma — yanıtı yerel Gemma ile yeniden ifade et (aç/kapat)\n/yardim — bu liste")  # fmt: skip
