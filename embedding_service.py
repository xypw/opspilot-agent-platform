"""本地中文 Embedding 服务；模型按需加载，避免 API 调用费用。"""

from fastembed import TextEmbedding


MODEL_NAME = "BAAI/bge-small-zh-v1.5"


class LocalEmbeddingService:
    """分别为文档片段和用户问题生成 512 维向量。"""

    def __init__(self, model=None):
        # 测试可注入假模型；真实模型只在第一次使用时下载并加载。
        self._model = model

    def _get_model(self):
        if self._model is None:
            self._model = TextEmbedding(model_name=MODEL_NAME)
        return self._model

    @staticmethod
    def _validate_texts(texts: list[str]) -> None:
        if not texts:
            raise ValueError("至少需要一段文本")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding 文本必须是非空字符串")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量生成文档向量；入库时调用一次，结果以后存入向量数据库。"""
        self._validate_texts(texts)
        vectors_generator = self._get_model().passage_embed(texts)
        vectors = list(vectors_generator)
        return [vector.tolist() for vector in vectors]

    def embed_query(self, query: str) -> list[float]:
        """生成一个查询向量；每次语义检索时调用。"""
        self._validate_texts([query])
        vectors = list(self._get_model().query_embed(query))
        if len(vectors) != 1:
            raise ValueError("查询 Embedding 数量不符合预期")
        return vectors[0].tolist()


def attach_embeddings(records: list[dict], service: LocalEmbeddingService) -> list[dict]:
    """为每个知识片段绑定同一位置的向量。"""
    if not records:
        return []
    if any(not isinstance(record.get("content"), str) or not record["content"].strip()
           for record in records):
        raise ValueError("每个知识片段都必须包含非空 content")

    texts = [record["content"] for record in records]
    vectors = service.embed_documents(texts)
    if len(records) != len(vectors):
        raise ValueError("知识片段数量与 Embedding 数量不一致")

    for record, vector in zip(records, vectors):
        record["embedding"] = vector
    return records
