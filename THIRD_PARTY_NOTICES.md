# Third-party notices

`qwen_tokenizer.py` adapts the non-thinking Qwen3-VL prompt rendering protocol
from Ollama v0.40.5 (`model/renderers/qwen3vl.go`, `json.go`, and API tool types).
Source: https://github.com/ollama/ollama/tree/v0.40.5/model/renderers

MIT License

Copyright (c) Ollama

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

The installed Qwen model vocabulary is exported locally, not redistributed as
model weights. The Qwen model metadata specifies Apache-2.0. The optional local
BGE ONNX cache comes from Qdrant/bge-small-zh-v1.5 (MIT); source revision and
checksums are saved beside the cache. Model files are excluded from Git.
