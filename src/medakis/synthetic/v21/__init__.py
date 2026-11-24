"""Sentetik üretici 2.1.0: 2.0.0 veri kümesinin üzerine planlı kadro katmanı (yalnız bellek).

2.0.0 ve 1.0.0 üretim mantığına dokunmaz; 2.0.0 tabloları ve içerik özeti aynen korunur. Plan katmanı
ayrı bir özetle (`plan_sha256`) izlenir. Optimizasyon modüllerini içe aktarmaz.
"""

from medakis.synthetic.v21.plan import GENERATOR_VERSION, DatasetV21, ParamsV21, generate_v21

__all__ = ["GENERATOR_VERSION", "DatasetV21", "ParamsV21", "generate_v21"]
