import re
from dataclasses import dataclass

from rank_bm25 import BM25Plus

from app.rag.knowledge_base import MOCK_KNOWLEDGE_BASE, KnowledgeDocument

_QUERY_SYNONYMS = {
    '多少钱': '价格 售价',
    '价格': '售价 价格',
    '怎么用': '使用 操作',
    '怎么清洗': '清洗 清洁',
    '清洁': '清洗 清洁',
    '保修': '质保 保修',
    '退换': '退换 退货',
}


@dataclass(frozen=True, slots=True)
class SearchResult:
    document: KnowledgeDocument
    score: float


def _tokenize(text: str) -> list[str]:
    normalized = text.lower()
    tokens = re.findall(r'[a-z0-9]+', normalized)
    for chunk in re.findall(r'[\u4e00-\u9fff]+', normalized):
        tokens.extend(chunk)
        tokens.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return tokens


def _expand_query(query: str) -> str:
    additions = [
        expansion
        for keyword, expansion in _QUERY_SYNONYMS.items()
        if keyword in query
    ]
    return ' '.join([query, *additions])


_DOCUMENT_TOKENS = [
    _tokenize(f'{document.title} {document.content}')
    for document in MOCK_KNOWLEDGE_BASE
]
_BM25_INDEX = BM25Plus(_DOCUMENT_TOKENS)


def search_knowledge_base(query: str, top_k: int = 1) -> list[SearchResult]:
    if top_k < 1:
        raise ValueError('top_k must be greater than zero')

    query_tokens = _tokenize(_expand_query(query))
    if not query_tokens:
        return []

    scores = _BM25_INDEX.get_scores(query_tokens)
    ranked = sorted(
        zip(scores, MOCK_KNOWLEDGE_BASE),
        key=lambda item: item[0],
        reverse=True,
    )
    return [
        SearchResult(document=document, score=float(score))
        for score, document in ranked[:top_k]
        if score > 0
    ]
