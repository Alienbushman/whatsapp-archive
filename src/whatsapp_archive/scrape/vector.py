import os

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

COLLECTION = "articles"
DIM = 768  # nomic-embed-text output dimension

_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")


def get_client(qdrant_url: str | None = None) -> QdrantClient:
    return QdrantClient(url=qdrant_url or _DEFAULT_QDRANT_URL)


def ensure_collection(client: QdrantClient) -> None:
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        client.create_collection(
            COLLECTION,
            vectors_config=VectorParams(size=DIM, distance=Distance.COSINE),
        )


def embed(text: str, ollama_url: str = _DEFAULT_OLLAMA_URL) -> list[float]:
    resp = httpx.post(
        f"{ollama_url}/api/embeddings",
        json={"model": "nomic-embed-text", "prompt": text[:4000]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def upsert_article_vector(
    client: QdrantClient,
    article_id: int,
    text: str,
    payload: dict,
    ollama_url: str = _DEFAULT_OLLAMA_URL,
) -> None:
    vec = embed(text, ollama_url)
    client.upsert(COLLECTION, points=[PointStruct(id=article_id, vector=vec, payload=payload)])


def _query(client: QdrantClient, vec, limit: int):
    """Compat wrapper for both old `.search()` and new `.query_points()` qdrant-client APIs."""
    if hasattr(client, "query_points"):
        resp = client.query_points(COLLECTION, query=vec, limit=limit)
        return resp.points
    return client.search(COLLECTION, query_vector=vec, limit=limit)


def search_similar(
    client: QdrantClient,
    query: str,
    limit: int = 10,
    ollama_url: str = _DEFAULT_OLLAMA_URL,
) -> list[dict]:
    vec = embed(query, ollama_url)
    hits = _query(client, vec, limit)
    return [{"id": h.id, "score": h.score, **(h.payload or {})} for h in hits]


def find_similar_articles(
    client: QdrantClient,
    article_id: int,
    limit: int = 10,
    exclude_self: bool = True,
) -> list[dict] | None:
    """Find articles whose stored vectors are closest to article_id's vector.

    Returns None when the article has no vector in Qdrant (not yet embedded).
    """
    points = client.retrieve(COLLECTION, ids=[article_id], with_vectors=True)
    if not points:
        return None
    vec = points[0].vector
    fetch_limit = limit + 1 if exclude_self else limit
    hits = _query(client, vec, fetch_limit)
    if exclude_self:
        hits = [h for h in hits if h.id != article_id]
    return [{"id": h.id, "score": h.score, **(h.payload or {})} for h in hits[:limit]]
