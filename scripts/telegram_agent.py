"""MedAkis Telegram agent'ını başlatır.

Kimlik bilgileri ortam değişkenlerinden (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) ya da Windows Credential Manager'daki
`medakis-telegram` kaydından okunur. Kayıt için (token gizli girişle alınır, yazdırılmaz):

    .\\.venv\\Scripts\\python.exe scripts\\telegram_agent.py --kaydet
    .\\.venv\\Scripts\\python.exe scripts\\telegram_agent.py            botu başlatır (Ctrl+C ile durur)
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from medakis.notifications.telegram import KEYRING_SERVICE, TelegramConfig  # noqa: E402


def save() -> None:
    import keyring

    token = getpass.getpass("Bot token (görünmez): ").strip()
    chat = input("Sohbet kimliği (chat id): ").strip()
    if not token or not chat.lstrip("-").isdigit():
        sys.exit("Token ya da sohbet kimliği geçersiz; kayıt yapılmadı.")
    keyring.set_password(KEYRING_SERVICE, "bot_token", token)
    keyring.set_password(KEYRING_SERVICE, "chat_id", chat)
    print("Kaydedildi (Windows Credential Manager → medakis-telegram).")


def main() -> None:
    if "--kaydet" in sys.argv:
        save()
        return
    cfg = TelegramConfig.from_env()
    if not (cfg.token and cfg.chat_id):
        sys.exit("Telegram kimliği yok. Önce: .\\.venv\\Scripts\\python.exe scripts\\telegram_agent.py --kaydet")
    from medakis.agent.telegram_bot import TelegramAgent
    from medakis.twin.hub import Engine

    print("Motor hazırlanıyor (sentetik veri, tahmin modelleri; ~40 sn)…")
    agent = TelegramAgent(Engine.build(), cfg)
    try:
        agent.run()
    except KeyboardInterrupt:
        print(f"Durduruldu. Yok sayılan yetkisiz mesaj: {agent.ignored}.")


if __name__ == "__main__":
    main()
