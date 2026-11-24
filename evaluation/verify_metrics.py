from pathlib import Path
import json,hashlib
def scores(ranked,gold):
    if not gold: return None,None
    ranked=list(dict.fromkeys(ranked))
    recall=len(set(ranked[:5])&set(gold))/len(set(gold))
    rr=next((1/r for r,x in enumerate(ranked[:10],1) if x in gold),0.0)
    return recall,rr
assert scores(['x','a','a','b'],['a','b'])==(1.0,0.5)
assert scores(['x'],['a'])==(0.0,0.0)
assert scores([],[])==(None,None)
assert scores(['x']*4+['a'],['a'])==(1.0,0.5)
assert scores([str(i) for i in range(10)]+['a'],['a'])==(0.0,0.0)
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'corpus/manifest.json').read_text(encoding='utf-8'))
keys=set()
for d in manifest:
    assert d['is_synthetic'] and d['status']=='draft'
    assert hashlib.sha256((root/'corpus'/d['path']).read_bytes()).hexdigest()==d['content_sha256']
    keys.update(s['gold_chunk_key'] for s in d['sections'])
cases=[json.loads(x) for x in (root/'evaluation/mini_gold.jsonl').read_text(encoding='utf-8').splitlines()]
assert len(cases)==12 and sum(c['answerable'] for c in cases)==8
for c in cases:
    assert set(c['relevant_chunk_keys'])<=keys
    assert bool(c['relevant_chunk_keys'])==c['answerable']
print(json.dumps({'metric_examples':5,'corpus_documents':len(manifest),'smoke_cases':len(cases),'status':'passed','model_quality_measured':False}))
