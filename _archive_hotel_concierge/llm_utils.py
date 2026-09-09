import os
import requests


def embed_text(text: str) -> list[float] | None:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    try:
        resp = requests.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "input": text},
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data["data"][0]["embedding"]
    except Exception:
        return None
