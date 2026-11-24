"""Sentetik kamera simülasyonu: hastane kat planında hareket eden anonim figürler.

Gerçek görüntü, yüz ya da kimlik kullanılmaz. Figürler hücre ızgarasında üst üste binmeden durur; böylece sentetik
karedeki sayım doğrulanabilir. `count_from_frame` yalnız sentetik karedeki renkli lekeleri sayar; gerçek insan tespiti
DEĞİLDİR.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

CELL, W, H = 12, 720, 420
PERSON_RGB = (20, 184, 166)


@dataclass(frozen=True)
class Area:
    key: str
    name: str
    capacity: int
    rect: tuple[int, int, int, int]  # x, y, genişlik, yükseklik (piksel)
    base: float  # normal senaryoda kapasiteye göre hedef doluluk

    def cells(self) -> list[tuple[int, int]]:
        x, y, w, h = self.rect
        return [(cx, cy) for cy in range(y + 18, y + h - CELL + 1, CELL) for cx in range(x + 6, x + w - CELL + 1, CELL)]


AREAS: tuple[Area, ...] = (
    Area("acil", "Acil Servis", 40, (10, 10, 340, 190), 0.55),
    Area("poliklinik", "Poliklinik", 50, (370, 10, 340, 190), 0.50),
    Area("yatakli", "Yataklı Servis", 30, (10, 220, 230, 190), 0.65),
    Area("bekleme", "Bekleme Alanı", 60, (250, 220, 250, 190), 0.45),
    Area("destek", "Destek Hizmetleri", 20, (510, 220, 200, 190), 0.40),
)
SCENARIOS = {"normal": "Normal gün", "acil_yogunluk": "Acil servis yoğunluk artışı", "aksam": "Akşam saatleri"}


def _target(area: Area, scenario: str, t: int) -> float:
    ratio = area.base
    if scenario == "acil_yogunluk":
        if area.key == "acil":
            ratio = min(1.05, area.base + 0.04 * t)  # her adımda artış
        elif area.key == "bekleme":
            ratio = min(0.95, area.base + 0.02 * t)
    elif scenario == "aksam":
        ratio = area.base * (0.6 if area.key == "poliklinik" else 0.9)
    return ratio * area.capacity


@dataclass
class Simulator:
    scenario: str = "normal"
    seed: int = 7
    t: int = 0
    people: dict[str, set[tuple[int, int]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.scenario not in SCENARIOS:
            raise ValueError(f"bilinmeyen senaryo: {self.scenario}")
        self.rng = random.Random(self.seed)
        for a in AREAS:
            cells = a.cells()
            n = min(len(cells), round(_target(a, self.scenario, 0)))
            self.people[a.key] = set(self.rng.sample(cells, n))

    def step(self) -> dict[str, int]:
        """Bir zaman adımı: alanlara giriş-çıkış ve alan içi hareket."""
        self.t += 1
        for a in AREAS:
            cells, occ = a.cells(), self.people[a.key]
            want = min(len(cells), max(0, round(_target(a, self.scenario, self.t) + self.rng.gauss(0, 1.5))))
            free = [c for c in cells if c not in occ]
            while len(occ) < want and free:  # giriş
                occ.add(free.pop(self.rng.randrange(len(free))))
            while len(occ) > want:  # çıkış
                occ.remove(self.rng.choice(sorted(occ)))
            for c in self.rng.sample(sorted(occ), k=len(occ) // 4):  # hareket
                nb = [(c[0] + dx, c[1] + dy) for dx, dy in ((CELL, 0), (-CELL, 0), (0, CELL), (0, -CELL))]
                nb = [n for n in nb if n in set(cells) and n not in occ]
                if nb:
                    occ.remove(c)
                    occ.add(self.rng.choice(nb))
        return self.counts()

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.people.items()}

    def frame(self) -> Image.Image:
        img = Image.new("RGB", (W, H), (11, 18, 32))
        d = ImageDraw.Draw(img)
        for a in AREAS:
            x, y, w, h = a.rect
            d.rectangle((x, y, x + w, y + h), fill=(17, 28, 46), outline=(56, 189, 248), width=1)
            d.text((x + 6, y + 3), f"{a.name}  [KAMERA-{a.key.upper()[:3]}]", fill=(203, 213, 225))
            for cx, cy in self.people[a.key]:
                d.ellipse((cx + 2, cy + 2, cx + CELL - 3, cy + CELL - 3), fill=PERSON_RGB)
        d.text((W - 210, H - 12), f"SENTETIK KAMERA SIMULASYONU  t={self.t}", fill=(245, 158, 11))
        return img


def count_from_frame(img: Image.Image) -> dict[str, int]:
    """Sentetik karede figür rengindeki bağlantılı bölgeleri alan bazında sayar (gerçek insan tespiti değildir)."""
    arr = np.asarray(img).astype(int)
    mask = (np.abs(arr - np.array(PERSON_RGB)).sum(axis=2) < 30)
    out = {}
    for a in AREAS:
        x, y, w, h = a.rect
        _, n = ndimage.label(mask[y:y + h, x:x + w])
        out[a.key] = int(n)
    return out
