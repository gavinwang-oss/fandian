import json
from typing import Optional

from db import list_hotel_docs, update_hotel_doc_embedding
from llm_utils import embed_text


class HotelVectorIndex:
    def __init__(self, hotel_id: int):
        self.hotel_id = hotel_id
        self._index = None
        self._doc_map = {}
        self._dim = None
        self._size = 0

    def build(self) -> bool:
        try:
            import hnswlib  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return False

        docs = list_hotel_docs(self.hotel_id, 2000)
        vectors = []
        ids = []

        for d in docs:
            vec = None
            if d["embedding_json"]:
                try:
                    vec = json.loads(d["embedding_json"])
                except Exception:
                    vec = None
            if not vec:
                vec = embed_text(f"{d['title']}\n{d['content']}")
                if vec:
                    update_hotel_doc_embedding(d["id"], json.dumps(vec))
            if vec:
                ids.append(d["id"])
                vectors.append(vec)
                self._doc_map[d["id"]] = d

        if not vectors:
            return False

        self._dim = len(vectors[0])
        data = np.array(vectors, dtype="float32")
        self._index = hnswlib.Index(space="cosine", dim=self._dim)
        self._index.init_index(max_elements=len(ids), ef_construction=200, M=32)
        self._index.add_items(data, ids)
        self._index.set_ef(100)
        self._size = len(ids)
        return True

    def query(self, query_text: str, k: int = 3):
        if self._index is None:
            return []
        if self._size == 0:
            return []
        vec = embed_text(query_text)
        if not vec:
            return []
        try:
            import numpy as np  # type: ignore
        except Exception:
            return []
        data = np.array([vec], dtype="float32")
        k = min(k, self._size)
        labels, distances = self._index.knn_query(data, k=k)
        results = []
        for doc_id, dist in zip(labels[0], distances[0]):
            d = self._doc_map.get(int(doc_id))
            if d is not None:
                # hnswlib returns cosine distance (0 is best)
                results.append((1.0 - float(dist), d))
        return results


_index_cache: dict[int, HotelVectorIndex] = {}


def get_vector_index(hotel_id: int) -> Optional[HotelVectorIndex]:
    idx = _index_cache.get(hotel_id)
    if idx is None:
        idx = HotelVectorIndex(hotel_id)
        if not idx.build():
            return None
        _index_cache[hotel_id] = idx
    return idx


def invalidate_vector_index(hotel_id: int) -> None:
    """Remove a hotel's vector index from cache so it rebuilds on next query."""
    _index_cache.pop(hotel_id, None)
