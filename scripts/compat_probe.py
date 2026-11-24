"""Python sürümü/bağımlılık çalışma zamanı uyumluluk yoklaması (ağsız, dosya yazmaz).

Kilitli paketlerin bu Python sürümünde gerçekten çalıştığını küçük örneklerle sınar:
scikit-learn RandomForest, pandas zaman örnekleme, OR-Tools CP-SAT NoOverlap ve
bellek içi Chroma (açık vektörlerle; varsayılan embedding indirmesi yok, telemetri kapalı).
Bu bir model/kalite testi DEĞİLDİR.

Kullanım: .\\.venv\\Scripts\\python.exe scripts\\compat_probe.py
"""
import platform

import time, numpy as np, pandas as pd
from sklearn.ensemble import RandomForestRegressor
from ortools.sat.python import cp_model
import chromadb
from chromadb.config import Settings
t=time.perf_counter()
rng=np.random.default_rng(42); X=rng.random((500,6)); y=X@np.arange(6)+rng.normal(0,.1,500)
rf=RandomForestRegressor(n_estimators=50,random_state=42,n_jobs=2).fit(X[:400],y[:400])
mae=float(np.mean(np.abs(rf.predict(X[400:])-y[400:])))
df=pd.DataFrame({"t":pd.date_range("2026-11-04T17:00Z",periods=8,freq="15min"),"m":range(8)}).set_index("t").resample("30min").sum()
m=cp_model.CpModel(); iv=[]
for i,d in enumerate([20,15,30]):
    s=m.new_int_var(0,120,f"s{i}"); iv.append(m.new_interval_var(s,d,s+d,f"i{i}"))
m.add_no_overlap(iv); sol=cp_model.CpSolver(); sol.parameters.max_time_in_seconds=5; st=sol.status_name(sol.solve(m))
c=chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
col=c.create_collection("probe",embedding_function=None,metadata={"hnsw:space":"cosine"})
col.add(ids=["a","b"],embeddings=[[1.0,0.0,0.0],[0.0,1.0,0.0]],documents=["x","y"])
q=col.query(query_embeddings=[[0.9,0.1,0.0]],n_results=2)
print({"python":platform.python_version(),"rf_mae":round(mae,3),"pandas_rows":len(df),"cpsat":st,"chroma_top":q["ids"][0][0],"chroma_dist":round(q["distances"][0][0],4),"sec":round(time.perf_counter()-t,2)})
