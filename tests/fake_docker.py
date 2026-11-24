"""Sahte docker komutu (A1 regresyon testleri; pytest bunu toplamaz).

Her çağrıyı FAKE_DOCKER_LOG dosyasına JSON satırı olarak yazar (argümanlar ve stdin). Gerçek Docker'a
hiçbir zaman bağlanmaz. Davranış ortam değişkenleriyle seçilir:
- FAKE_DOCKER_RUNNING: "true" ise `docker inspect --format ...` çalışan bir prova konteyneri bildirir.
- `docker compose ...` her zaman GERÇEK konteyneri (medakis-postgres) seçiyormuş gibi yanıt verir:
  hedefi Compose'dan yeniden seçen bir betik bu yanıtı kullanırdı.
"""

import json
import os
import sys


def main() -> int:
    args = sys.argv[1:]
    stdin = ""
    if args[:2] in (["exec", "-i"], ["compose", "exec"]):
        stdin = sys.stdin.read()
    with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"args": args, "stdin": stdin}) + "\n")
    if args[:1] == ["compose"]:
        print("medakis-postgres")
        return 0
    if args[:1] == ["inspect"]:
        if os.environ.get("FAKE_DOCKER_RUNNING") == "true":
            print("true /medakis-prova-postgres")
            return 0
        print("Error: No such object", file=sys.stderr)
        return 1
    if args[:2] == ["exec", "-i"]:
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
