# 本地Ollama运行

普通用户访问 http://127.0.0.1:8011/demo 登录，不需要选择工具、供应商或模拟模式。

1. 打开本机Ollama，确认 `ollama list` 中存在 `qwen3-vl:4b`。
2. 启动Docker Desktop，再从项目根目录启动Compose。
3. 更新后用Ctrl+F5刷新旧JS。

## 首次准备本地资源

在项目根目录使用Python 3.12+安装依赖；此步骤与模型获取会下载文件，先确认网络和磁盘空间。已有本机模型/资源时不要重复下载。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 尚未安装本地模型时才执行，约3.3GB
ollama pull qwen3-vl:4b
$env:OLLAMA_BASE_URL='http://127.0.0.1:11434'
$env:OLLAMA_MODEL='qwen3-vl:4b'
.\.venv\Scripts\python.exe scripts/export_context_tokenizer.py
# 固定版本的BGE ONNX及配置合计约96MB；仅本地嵌入，不上传会话
.\.venv\Scripts\python.exe scripts/cache_memory_embedding.py
```

导出脚本只读取已安装模型的词表/版本元数据，不进行推理；当前校准支持Ollama 0.40.5/0.40.9，未知版本须核对模板并重新校准后才能启用精确计数。运行时不会自动下载缺失资源或切换云端。

Compose 将 `.models/context-tokenizer` 和 `.models/memory-bge` 只读挂载到API。Python直接启动时，另配置 `QWEN_TOKENIZER_PATH` 与 `MEMORY_EMBEDDING_PATH` 为对应本机绝对目录。新部署按README初始化账号和003/004迁移；已有数据卷执行初始化脚本只补缺失表，不重置账号。

默认Compose使用 `OPSPILOT_MODEL_PROVIDER=ollama`，通过 `host.docker.internal:11434` 访问Windows宿主机；Python直跑默认 `http://127.0.0.1:11434`。地址与模型只由服务端配置，用户不能传入模型URL。

- 仍通过本机HTTP API调用，不向智谱外发、不使用云端Key、不自动切换供应商。模型未运行时明确失败，不回退模拟回答。
- 工具选择、图片观察和证据审查均接入本地模型；问候和缺编号指引不必请求模型。本地模式禁用旧SiliconFlow Key对应的云端重排，保留检索和RRF。
- 本地省去供应商费用，但仍占用显存、内存与电力，不保证更快。当前一条政策任务约64秒，不能承诺实时或普遍正确。
- Java仍校验归属、资格、事务和幂等；正式申请必须用户确认。图片路径没有业务写执行器。
- 智谱仅保留为服务端显式选项，需另行配置和授权；普通用户页面不提供切换。历史云端429报告不代表此次本地结果。

具体结果见[本地修复报告](../reports/local-model-bugfix-20261022.md)。
