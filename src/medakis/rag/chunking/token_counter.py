"""Model bağımsız, deterministik token TAHMİNİ (parçalama için).

İki embedding adayı aynı parça kümesini (snapshot) kullanmalıdır; bu yüzden parçalama modelin tokenizer'ına bağlı
değildir. Kesin sayı indeksleme sırasında modelin kendisinden (Ollama `prompt_eval_count`, parça başına tek istek)
alınır ve `ChunkConfig.max_input_tokens` sınırını aşan parça indeksi durdurur (sessiz kesme yok).

Tahmin bilerek cömerttir: her sözcük için ceil(uzunluk/4) parça, her noktalama için 1, ayrıca 2 özel token.
"""

from __future__ import annotations

import math
import re
from typing import Protocol

_WORD = re.compile(r"\w+", re.UNICODE)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
NAME = "yaklasik_v1"


class TokenCounter(Protocol):
    name: str

    def count(self, text: str) -> int: ...


class ApproxTokenCounter:
    name = NAME

    def count(self, text: str) -> int:
        words = sum(math.ceil(len(w) / 4) for w in _WORD.findall(text))
        return words + len(_PUNCT.findall(text)) + 2
