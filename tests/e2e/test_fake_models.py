"""The fake model server speaks what the real clients send (ollama client, Embedder, ChatClient)."""

from __future__ import annotations

import asyncio
import json

import httpx

from core.config import get_settings
from core.extractor import LLMExtractor, llm_schema
from core.forms import W2
from tests.e2e.fake_models import serve


def test_fake_models_answer_extraction_embeddings_and_chat():
    server = serve(0)
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        extractor = LLMExtractor(get_settings().model_copy(update={"ollama_url": url}))
        evidence = "1 Wages => 68,412.55\nd Control number => A7731-0042"
        prompt = f"Document type: {W2.TITLE}\n\n<document>\n{evidence}\n</document>"
        answer = json.loads(asyncio.run(extractor._chat([{"role": "user", "content": prompt}], llm_schema(W2))))
        assert answer["wages_tips_other_comp"] == 69412.55  # altered: verification will flag it not_printed
        assert answer["control_number"] == "A7731-0042 | X"  # altered: multiple_values
        assert asyncio.run(extractor._chat([{"role": "user", "content": "Reply with {}"}], {"type": "object"})) == "{}"

        tags = httpx.get(f"{url}/api/tags").json()["models"]
        assert "qwen3.5:4b" in {t["name"] for t in tags}
        vectors = httpx.post(f"{url}/v1/embeddings", json={"input": ["wages", "wages"]}).json()["data"]
        assert vectors[0]["embedding"] == vectors[1]["embedding"] and len(vectors[0]["embedding"]) == 256
        call = httpx.post(f"{url}/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "wages?"}], "tools": [{}]}).json()["choices"][0]["message"]
        assert call["tool_calls"][0]["function"]["name"] == "search_documents"
    finally:
        server.shutdown()
