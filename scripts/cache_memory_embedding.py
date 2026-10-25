"""Explicit, bounded download of the single authorized BGE ONNX archive."""
import hashlib
import json
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
REPOSITORY='Qdrant/bge-small-zh-v1.5'
REVISION='46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59'
ALLOWED=('model_optimized.onnx','config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','vocab.txt')


def cache():
    target=ROOT/'.models/memory-bge';target.mkdir(parents=True,exist_ok=True)
    total=0;hashes={}
    with httpx.Client(timeout=httpx.Timeout(45,connect=10),follow_redirects=True) as client:
        revision=REVISION
        for name in ALLOWED:
            url='https://hf.co/'+REPOSITORY+'/resolve/'+revision+'/'+name
            with client.stream('GET',url) as response:
                response.raise_for_status();size=0
                temporary=target/(name+'.part')
                with temporary.open('wb') as output:
                    for chunk in response.iter_bytes():
                        size+=len(chunk);total+=len(chunk)
                        if total>100*1024*1024:raise ValueError('下载超过已授权上限')
                        output.write(chunk)
                if response.headers.get('content-length') and size!=int(response.headers['content-length']):
                    raise ValueError('下载文件不完整')
                temporary.replace(target/name)
                hashes[name]=hashlib.sha256((target/name).read_bytes()).hexdigest()
    if not (target/'model_optimized.onnx').exists():raise ValueError('下载包缺少ONNX模型')
    manifest={'source':'https://huggingface.co/'+REPOSITORY,'revision':revision,'model':'BAAI/bge-small-zh-v1.5',
              'sha256':hashes,'downloaded_bytes':total,
              'files':{p.name:p.stat().st_size for p in target.iterdir() if p.is_file()}}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest))


if __name__=='__main__':cache()
