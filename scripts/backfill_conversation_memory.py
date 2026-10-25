"""Explicit local administration: bounded source-only semantic backfill."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from conversation_memory import ConversationMemory,cached_embedder
from model_runtime import setting
from portal_store import PostgresPortalStore

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--user-id',required=True);parser.add_argument('--conversation-id',required=True)
    parser.add_argument('--max-batches',type=int,default=10)
    args=parser.parse_args()
    if not 1<=args.max_batches<=100:parser.error('max-batches须在1至100之间')
    service=ConversationMemory(cached_embedder(setting('MEMORY_EMBEDDING_PATH')))
    store=PostgresPortalStore();count=0
    for _ in range(args.max_batches):
        added=service.index_pending(store,args.user_id,args.conversation_id)
        count+=added
        if not added:break
    print({'indexed_source_messages':count,'llm_requests':0,'source_messages_modified':False})
