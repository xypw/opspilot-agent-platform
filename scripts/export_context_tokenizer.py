"""Export BPE from an already installed local model; no weight downloads."""
import hashlib
import json
from pathlib import Path
import sys

import httpx
from tokenizers import AddedToken, Regex, Tokenizer, models, pre_tokenizers, decoders

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

QWEN2_PATTERN=r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"


def export(target):
    from model_runtime import ollama_configuration
    base,name=ollama_configuration()
    with httpx.Client(timeout=15,trust_env=False) as client:
        response=client.post(base+'/api/show',json={'model':name,'verbose':True})
        response.raise_for_status();data=response.json()
        version=client.get(base+'/api/version').json()['version']
    info=data['model_info']
    from qwen_tokenizer import VERIFIED_OLLAMA_VERSIONS
    if version not in VERIFIED_OLLAMA_VERSIONS:raise ValueError('先校验新版本模板，不能自动导出未知协议')
    if info.get('tokenizer.ggml.pre')!='qwen2' or name!='qwen3-vl:4b':raise ValueError('仅导出已核实Qwen3-VL模型')
    vocab=info['tokenizer.ggml.tokens'];types=info['tokenizer.ggml.token_type']
    if len(set(vocab))!=len(vocab):raise ValueError('词表存在重复项')
    merges=[tuple(item.split(' ',1)) for item in info['tokenizer.ggml.merges']]
    tokenizer=Tokenizer(models.BPE(vocab={value:i for i,value in enumerate(vocab)},merges=merges))
    tokenizer.pre_tokenizer=pre_tokenizers.Sequence([
        pre_tokenizers.Split(Regex(QWEN2_PATTERN),behavior='isolated'),
        pre_tokenizers.ByteLevel(add_prefix_space=False,use_regex=False)])
    tokenizer.decoder=decoders.ByteLevel()
    tokenizer.add_special_tokens([AddedToken(value,special=True,normalized=False)
        for value,kind in zip(vocab,types) if kind in {3,4}])
    target.mkdir(parents=True,exist_ok=True)
    tokenizer.save(str(target/'tokenizer.json'))
    manifest={'model':name,'ollama_version':version,'pre_tokenizer':'qwen2',
              'renderer':'qwen3-vl-thinking','vocab_size':len(vocab),
              'sha256':hashlib.sha256((target/'tokenizer.json').read_bytes()).hexdigest(),
              'source':'installed model metadata; no weights downloaded'}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest))


if __name__=='__main__':export(ROOT/'.models/context-tokenizer')
