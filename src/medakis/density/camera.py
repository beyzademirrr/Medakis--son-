"""Sentetik hastane kamerası ve figür dedektörü.

Görüntü tamamen programatik üretilir (gerçek kişi, yüz ya da kimlik yoktur). Figür sayısı birimin o andaki aktif hasta
kaydından ve kat/kamera varsayımından türetilir (simülasyon gerçeği). Dedektör yalnız sentetik karede çalışır:
açık renkli figür piksellerini eşikler, bağlı bileşenleri bulur ve kutu çizer. Figürler ayrı yerleşim yuvalarında
durur; ısı ve kutu katmanı yalnız gösterimdir. Gerçek görüntüde temas eden kişiler tek kutuya birleşebilir; bu yüzden dedektör sayımı simülasyon gerçeğinden sapabilir ve sapma ayrıca raporlanır. Bu yöntem gerçek
hastane kamerasında doğrulanmış insan tespiti DEĞİLDİR.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

W, H = 320, 220
FIG = (226, 232, 240)


@dataclass(frozen=True)
class Detection:
    count: int
    boxes: tuple[tuple[int, int, int, int], ...]


def _rng(*key: object) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, key)).encode()).hexdigest()[:12], 16))


SLOT_W, SLOT_H = 25, 36
SLOTS = [(x, y) for y in range(66, H - SLOT_H, SLOT_H) for x in range(8, W - SLOT_W, SLOT_W)]


def positions(unit_key: str, moment_key: str, minute: int, n: int) -> list[tuple[int, int]]:
    """n figürün `minute` anındaki konumları. Her figür bir yerleşim yuvasında durur ve yuvası içinde hareket eder;
    figürler yuvalar arasında yer değiştirir (giriş/çıkış). Yuvalar çakışmaz (deterministik)."""
    n = min(n, len(SLOTS))
    order = _rng(unit_key, moment_key, minute // 10).sample(range(len(SLOTS)), len(SLOTS))
    out = []
    for i, slot in enumerate(order[:n]):
        x, y = SLOTS[slot]
        r = _rng(unit_key, moment_key, i, minute)
        out.append((x + r.randint(0, SLOT_W - 17), y + r.randint(0, 3)))
    return out


_ASCII = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iIsSgGuUoOcC")  # gömülü bitmap yazı tipi Türkçe karakterleri çizemez


def render(title: str, unit_key: str, moment_key: str, minute: int, n: int, clock: str) -> Image.Image:
    title = title.translate(_ASCII)
    img = Image.new("RGB", (W, H), (22, 30, 44))
    d = ImageDraw.Draw(img)
    for x in range(0, W, 40):  # zemin karoları
        d.line((x, 40, x - 60, H), fill=(30, 40, 58))
    d.rectangle((0, 0, W, 34), fill=(12, 18, 30))
    d.rectangle((8, 44, 70, 60), fill=(45, 58, 80))  # bekleme sırası
    d.rectangle((W - 90, 44, W - 10, 70), fill=(45, 58, 80))  # bankolar
    for (x, y) in positions(unit_key, moment_key, minute, n):
        d.ellipse((x + 3, y - 9, x + 11, y - 1), fill=FIG)
        d.rounded_rectangle((x, y, x + 14, y + 22), radius=4, fill=FIG)
    d.text((8, 6), title, fill=(203, 213, 225))
    d.text((8, 20), "SENTETIK KAMERA · gercek kisi/hasta yok", fill=(245, 158, 11))
    d.text((W - 52, 12), clock, fill=(248, 250, 252))
    return img


def detect(img: Image.Image) -> Detection:
    arr = np.asarray(img).astype(int)
    mask = (arr.min(axis=2) > 200)
    mask[:36, :] = False  # başlık şeridi
    labels, n = ndimage.label(mask)
    boxes = []
    for sl in ndimage.find_objects(labels):
        if sl is None:
            continue
        ys, xs = sl
        if (ys.stop - ys.start) * (xs.stop - xs.start) >= 60:  # gürültü değil, figür boyutunda
            boxes.append((xs.start, ys.start, xs.stop, ys.stop))
    return Detection(len(boxes), tuple(boxes))


def overlay(img: Image.Image, det: Detection, view: str) -> Image.Image:
    """Görünümler: 'video' (ham), 'kutular' (tespit kutuları), 'isi' (yoğunluk parlaması), 'hepsi'."""
    out = img.copy()
    if view in ("isi", "hepsi") and det.boxes:
        heat = np.zeros((H, W))
        for x0, y0, x1, y1 in det.boxes:
            heat[(y0 + y1) // 2, (x0 + x1) // 2] += 1.0
        heat = ndimage.gaussian_filter(heat, sigma=14)
        heat = heat / heat.max() if heat.max() > 0 else heat
        rgba = np.zeros((H, W, 4), dtype=np.uint8)
        rgba[..., 0] = 239
        rgba[..., 1] = (68 + 120 * (1 - heat)).astype(np.uint8)
        rgba[..., 2] = 68
        rgba[..., 3] = (170 * heat).astype(np.uint8)
        out = Image.alpha_composite(out.convert("RGBA"), Image.fromarray(rgba, "RGBA")).convert("RGB")
    if view in ("kutular", "hepsi"):
        d = ImageDraw.Draw(out)
        for x0, y0, x1, y1 in det.boxes:
            d.rectangle((x0 - 1, y0 - 1, x1 + 1, y1 + 1), outline=(34, 197, 94), width=1)
    return out
