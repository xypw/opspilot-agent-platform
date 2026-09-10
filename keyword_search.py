"""关键词检索基线：擅长业务编号、错误码和精确词语。"""

import re


def search_terms(text: str) -> set[str]:
    """英文保留完整词；连续中文生成二元词片。"""
    normalized = text.lower().strip()
    terms = set(re.findall(r"[a-z0-9_-]+", normalized))
    for chinese_text in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(chinese_text) == 1:
            terms.add(chinese_text)
        else:
            terms.update(
                chinese_text[index:index + 2]
                for index in range(len(chinese_text) - 1)
            )
    return terms


def keyword_search(query: str, records: list[dict], limit: int = 3) -> list[dict]:
    """按查询词与标题、正文的重叠数量排序。"""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须是非空字符串")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit 必须是正整数")

    query_terms = search_terms(query)
    results = []
    for record in records:
        searchable_text = f"{record.get('title', '')} {record.get('content', '')}"
        score = len(query_terms & search_terms(searchable_text))
        if score == 0:
            continue
        result = {key: value for key, value in record.items() if key != "embedding"}
        result["keyword_score"] = score
        results.append(result)

    results.sort(key=lambda item: (-item["keyword_score"], item["chunk_id"]))
    return results[:limit]
