"""D5 model kimlikleri (onaylı dört model; revision sabit). İzole ortamda (`D:\\medakis-deney\\d5\\.venv`) kullanılır."""

MODELS = {
    "qwen06": {"repo": "Qwen/Qwen3-Reranker-0.6B", "revision": "e61197ed45024b0ed8a2d74b80b4d909f1255473", "tur": "qwen",
               "dosyalar": ["*.json", "*.txt", "*.jinja", "README.md", "model.safetensors"]},
    "modernbert": {"repo": "ytu-ce-cosmos/modernbert-tr-reranker", "revision": "d6aabbe061f1bf6cb796e317ecb6d9b8f7b96c54", "tur": "encoder",
                   "dosyalar": ["*.json", "README.md", "model.safetensors"]},
    "lamar": {"repo": "nlpai-lab/LAMAR-600m", "revision": "3af1f97945392da8ef46217640641355928aeb59", "tur": "encoder",
              "dosyalar": ["*.json", "README.md", "model.safetensors"]},
    "bge": {"repo": "BAAI/bge-reranker-v2-m3", "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e", "tur": "encoder",
            "dosyalar": ["*.json", "README.md", "sentencepiece.bpe.model", "model.safetensors"]},
}  # fmt: skip

# Qwen3-Reranker talimatları (önceden sabit; dev verisinde AYAR YAPILMAZ)
INSTRUCT_DEFAULT = "Given a web search query, retrieve relevant passages that answer the query"  # model kartı varsayılanı (R4)
INSTRUCT_ANSWERABLE = ("Judge whether the Document explicitly contains the information needed to answer the Query; "
                       "a document that is only on the same topic does not count.")  # R5 (D5 hazırlık raporu §6.1)  # fmt: skip
MAX_LENGTH = 512  # bütün modellerde aynı üst sınır; kesilen çiftler kaydedilir (v1 parçaları ≤ 496 karakter)
