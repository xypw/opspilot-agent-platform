"""Persistent semantic recall of owner-scoped source messages, never authority."""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import psycopg

from fastembed import TextEmbedding
from model_runtime import setting

MEMORY_MODEL='BAAI/bge-small-zh-v1.5'


class ConversationMemoryUnavailable(RuntimeError):
    pass


def source_hash(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()


def vector_literal(vector):
    if len(vector)!=512 or any(type(v) not in {float,int} or not math.isfinite(v) for v in vector):
        raise ValueError('语义记忆向量必须为512个有限数值')
    return json.dumps(vector,separators=(',',':'))


@lru_cache(maxsize=2)
def cached_embedder(path):
    root=Path(path)
    if not (root/'model_optimized.onnx').is_file():raise ValueError('语义记忆本地权重缺失；不会自动下载')
    return TextEmbedding(model_name=MEMORY_MODEL,specific_model_path=str(root),local_files_only=True,
                         threads=2,providers=['CPUExecutionProvider'])


class ConversationMemory:
    def __init__(self,model):self.model=model

    def index_pending(self,store,user,cid,limit=32):
        rows=store.unindexed_memory(user,cid,MEMORY_MODEL,limit)
        if not rows:return 0
        for row in rows:
            # Index bounded overlapping pieces, but always recall original source text.
            pieces=[row['message'][i:i+240] for i in range(0,len(row['message']),200)]
            vectors=[vector.tolist() if hasattr(vector,'tolist') else vector
                     for vector in self.model.passage_embed(pieces)]
            if len(vectors)!=len(pieces):raise ValueError('语义记忆向量数量不匹配')
            for vector in vectors:vector_literal(vector)
            store.save_memory(user,cid,str(row['id']),source_hash(row['message']),MEMORY_MODEL,vectors)
        return len(rows)

    def recall(self,store,user,cid,question):
        vectors=list(self.model.query_embed([question]))
        if len(vectors)!=1:raise ValueError('语义记忆查询向量数量不匹配')
        vector=vectors[0].tolist() if hasattr(vectors[0],'tolist') else vectors[0]
        vector_literal(vector)
        rows=store.search_memory(user,cid,MEMORY_MODEL,vector,8)
        # Read original text, not a paraphrased memory or stale model assertion.
        return [{**row,'_semantic_recalled':True} for row in rows
                if row.get('memory_source_hash')==source_hash(row['message'])]


def memory_context(store,user,cid,question,recent):
    path=setting('MEMORY_EMBEDDING_PATH')
    if not path:return recent
    try:
        service=ConversationMemory(cached_embedder(path))
        service.index_pending(store,user,cid)
        recalled=service.recall(store,user,cid,question)
    except Exception:
        # This boundary only updates derived memory, before any business tool runs.
        raise ConversationMemoryUnavailable('会话语义索引暂不可用，请检查本地权重和数据库迁移；未重发任务。') from None
    by_id={str(row['id']):row for row in recent}
    for row in recalled:
        by_id[str(row['id'])]={**by_id.get(str(row['id']),{}),**row}
    return sorted(by_id.values(),key=lambda row:(row['created_at'],str(row['id'])))
