"""Tests of the knowledge base: chunking, setup(), embed(), retrieve() and query(). No live Qdrant and no live model are needed."""

import hashlib
import math
import re
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient

from data.pipelines import rag
from data.process import embeddings, knowledge_base, model_client, rag_chunking
from data.process import rag_config as config


# ------------------------------------------------------------------ helpers
class StubQdrant:
    """What retrieve() needs from the Qdrant client: `query_points`. Returns canned hits and records how it was called."""

    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def query_points(self, collection, query, limit, with_payload):
        self.calls.append({"collection": collection, "query": query, "limit": limit})
        return SimpleNamespace(points=self.hits[:limit])


def hit(score, **payload):
    base = {"company": "trackflow", "source_document": "sla-delivery", "section": "Delivery SLA > x", "language": "en", "chunk_index": 0, "text": "chunk text"}
    return SimpleNamespace(score=score, payload={**base, **payload})


def fake_embed(text: str) -> list[float]:
    """A deterministic bag-of-words vector (64 dims). It tests the mechanics, not the quality of a real embeddings model."""
    vector = [0.0] * 64
    for word in re.findall(r"[a-z0-9%]+", text.lower()):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


# ------------------------------------------------------------------ chunking
def test_every_source_document_is_loaded_and_makes_at_least_three_chunks():
    chunks = rag_chunking.load_chunks()
    by_doc = {}
    for chunk in chunks:
        by_doc.setdefault(chunk.source_document, []).append(chunk)
    assert set(by_doc) == {"sla-delivery", "returns-policy", "carrier-coverage", "storage-pricing"}
    assert all(len(group) >= 3 for group in by_doc.values())  # CONTEXT section 5


def test_the_documents_come_from_the_knowledge_base_folder():
    assert config.KNOWLEDGE_BASE_DIR.name == "company-knowledge-base" and config.KNOWLEDGE_BASE_DIR.parent.name == "docs"
    for file_name in config.SOURCE_DOCUMENTS:
        assert (config.KNOWLEDGE_BASE_DIR / file_name).is_file()


def test_the_payload_has_the_fields_of_the_context_and_nothing_else():
    for chunk in rag_chunking.load_chunks():
        payload = chunk.payload()
        assert set(payload) == {"company", "source_document", "section", "language", "chunk_index", "text"}
        assert payload["company"] == "trackflow" and payload["language"] == "en"
        assert payload["section"] and payload["text"] and isinstance(payload["chunk_index"], int)


def test_no_text_of_a_document_is_lost_and_no_chunk_is_cut_mid_sentence():
    for file_name in config.SOURCE_DOCUMENTS:
        source = (config.KNOWLEDGE_BASE_DIR / file_name).read_text(encoding="utf-8")
        body = " ".join(source.split("\n", 1)[1].split())
        chunks = rag_chunking.chunk_document(source, config.SOURCE_DOCUMENTS[file_name])
        joined = " ".join(" ".join(chunk.text.split()) for chunk in chunks)
        assert joined == body, file_name  # same words, same order: nothing dropped, nothing cut in two
        for chunk in chunks:
            assert chunk.text.rstrip()[-1] in ".):" or chunk.text.rstrip().endswith(")."), chunk.text[-40:]
            assert not chunk.text.rstrip().endswith(":") or "\n" in chunk.text  # an introduction never stands alone


def test_a_list_stays_with_its_introduction_and_a_rule_keeps_its_condition():
    by_key = {(c.source_document, c.chunk_index): c for c in rag_chunking.load_chunks()}
    services = by_key[("sla-delivery", 0)].text
    assert all(term in services for term in ("Standard shipping", "Express shipping", "International shipping"))
    process = by_key[("returns-policy", 0)].text
    assert "1." in process and "2." in process and "3." in process  # the three steps stay together
    assert "manual handling" in by_key[("returns-policy", 3)].text and "not covered" in by_key[("returns-policy", 3)].text
    assert "40%" in by_key[("sla-delivery", 2)].text and "Black Friday" in by_key[("sla-delivery", 2)].text
    assert "Miguel Torres" in by_key[("storage-pricing", 2)].text
    assert "Carlos" in by_key[("carrier-coverage", 2)].text


def test_point_ids_are_deterministic_and_unique():
    first, second = rag_chunking.load_chunks(), rag_chunking.load_chunks()
    assert [c.point_id for c in first] == [c.point_id for c in second]
    assert len({c.point_id for c in first}) == len(first)


def test_a_document_that_makes_fewer_than_three_chunks_is_rejected(tmp_path):
    for file_name in config.SOURCE_DOCUMENTS:
        (tmp_path / file_name).write_text("# T\n\nOne single paragraph.\n", encoding="utf-8")
    with pytest.raises(ValueError):
        rag_chunking.load_chunks(tmp_path)


def test_a_missing_source_document_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        rag_chunking.load_chunks(tmp_path)


# ------------------------------------------------------------------ setup()
def test_setup_creates_the_collection_and_indexes_every_chunk_with_its_source_metadata():
    client = QdrantClient(":memory:")
    summary = knowledge_base.setup(client, embed_fn=fake_embed)
    assert summary["collection"] == "trackflow_knowledge" and summary["collection_action"] == "created"
    assert summary["vector_size"] == 64 and summary["distance"] == "Cosine"
    points = client.scroll("trackflow_knowledge", limit=100, with_payload=True)[0]
    assert len(points) == summary["chunks"] == len(rag_chunking.load_chunks())
    for point in points:
        assert point.payload["source_document"] and point.payload["section"] and point.payload["text"]


def test_setup_is_idempotent_running_it_again_does_not_duplicate_points():
    client = QdrantClient(":memory:")
    knowledge_base.setup(client, embed_fn=fake_embed)
    before = client.count("trackflow_knowledge").count
    again = knowledge_base.setup(client, embed_fn=fake_embed)
    assert again["collection_action"] == "reused" and client.count("trackflow_knowledge").count == before


def test_setup_removes_points_of_chunks_that_no_longer_exist(tmp_path):
    client = QdrantClient(":memory:")
    knowledge_base.setup(client, embed_fn=fake_embed)
    for file_name in config.SOURCE_DOCUMENTS:  # the same documents, one paragraph shorter
        text = (config.KNOWLEDGE_BASE_DIR / file_name).read_text(encoding="utf-8").rstrip()
        (tmp_path / file_name).write_text(text + "\n\nAn extra rule that closes the document.\n", encoding="utf-8")
    knowledge_base.setup(client, directory=tmp_path, embed_fn=fake_embed)
    grown = client.count("trackflow_knowledge").count
    summary = knowledge_base.setup(client, embed_fn=fake_embed)  # back to the real documents
    assert summary["stale_deleted"] == 4 and client.count("trackflow_knowledge").count == grown - 4


def test_setup_recreates_the_collection_when_the_embeddings_model_changes_the_vector_size():
    client = QdrantClient(":memory:")
    knowledge_base.setup(client, embed_fn=fake_embed)
    summary = knowledge_base.setup(client, embed_fn=lambda text: fake_embed(text)[:32])
    assert summary["collection_action"] == "recreated" and summary["vector_size"] == 32


def test_setup_changes_nothing_if_the_embeddings_model_fails():
    client = QdrantClient(":memory:")

    def broken(text):
        raise model_client.ModelServiceError("down")

    with pytest.raises(model_client.ModelServiceError):
        knowledge_base.setup(client, embed_fn=broken)
    assert not client.collection_exists("trackflow_knowledge")


# ------------------------------------------------------------------ embed()
def test_embed_calls_the_embeddings_model_and_not_the_generation_model(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "http://models.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("EMBEDDING_MODEL", "embed-model")
    monkeypatch.setenv("LLM_MODEL", "chat-model")
    sent = []
    monkeypatch.setattr(model_client, "post", lambda path, payload: sent.append((path, payload)) or {"data": [{"embedding": [0.1, 0.2]}]})
    assert embeddings.embed("  two   spaces\nand a newline ") == [0.1, 0.2]
    path, payload = sent[0]
    assert path == "/embeddings" and payload["model"] == "embed-model" and payload["input"] == "two spaces and a newline"


def test_embed_refuses_to_use_the_same_model_as_the_generation_model(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "same")
    monkeypatch.setenv("LLM_MODEL", "same")
    with pytest.raises(model_client.ModelServiceError):
        embeddings.embed("hello")


def test_the_model_service_errors_never_include_the_key_or_the_text(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("LLM_API_KEY", "super-secret-key")
    with pytest.raises(model_client.ModelServiceError) as error:
        model_client.post("/embeddings", {"input": "private question"})
    assert "super-secret-key" not in str(error.value) and "private question" not in str(error.value)


# ------------------------------------------------------------------ retrieve()
def test_retrieve_excludes_the_hits_below_min_score():
    client = StubQdrant([hit(0.9, chunk_index=0), hit(0.6, chunk_index=1), hit(0.2, chunk_index=2)])
    results = rag.retrieve("anything", k=5, min_score=0.5, client=client, embed_fn=fake_embed)
    assert [r["chunk_index"] for r in results] == [0, 1]


def test_retrieve_can_return_fewer_than_k_and_even_none():
    client = StubQdrant([hit(0.9), hit(0.1), hit(0.1)])
    assert len(rag.retrieve("q", k=5, min_score=0.5, client=client, embed_fn=fake_embed)) == 1  # k=5, only one clears the bar
    assert rag.retrieve("q", k=5, min_score=0.95, client=client, embed_fn=fake_embed) == []  # nothing: it does not force bad context


def test_retrieve_never_returns_more_than_k_and_asks_qdrant_for_k():
    client = StubQdrant([hit(0.9 - i * 0.01, chunk_index=i) for i in range(10)])
    results = rag.retrieve("q", k=3, min_score=0.0, client=client, embed_fn=fake_embed)
    assert len(results) == 3 and client.calls[0]["limit"] == 3 and client.calls[0]["collection"] == "trackflow_knowledge"


def test_a_score_equal_to_min_score_is_kept():
    assert len(rag.retrieve("q", k=5, min_score=0.5, client=StubQdrant([hit(0.5)]), embed_fn=fake_embed)) == 1


def test_retrieve_returns_plain_dicts_with_the_source_metadata_and_the_score():
    client = StubQdrant([hit(0.8, source_document="storage-pricing", section="Storage Pricing > Inventory aging report", chunk_index=1)])
    (result,) = rag.retrieve("q", k=5, min_score=0.1, client=client, embed_fn=fake_embed)
    assert type(result) is dict
    assert result["source_document"] == "storage-pricing" and result["section"].endswith("Inventory aging report") and result["text"] and result["score"] == 0.8


def test_retrieve_embeds_the_question_with_embed_and_searches_with_that_vector():
    seen = []
    client = StubQdrant([])
    rag.retrieve("Black Friday", k=2, min_score=0.1, client=client, embed_fn=lambda text: seen.append(text) or [1.0, 0.0])
    assert seen == ["Black Friday"] and client.calls[0]["query"] == [1.0, 0.0]


def test_retrieve_rejects_a_k_below_one():
    with pytest.raises(ValueError):
        rag.retrieve("q", k=0, min_score=0.1, client=StubQdrant([]), embed_fn=fake_embed)


def test_retrieve_uses_the_threshold_of_the_environment_when_none_is_given(monkeypatch):
    monkeypatch.setenv("RAG_MIN_SCORE", "0.7")
    client = StubQdrant([hit(0.8, chunk_index=0), hit(0.6, chunk_index=1)])
    assert [r["chunk_index"] for r in rag.retrieve("q", client=client, embed_fn=fake_embed)] == [0]


def test_end_to_end_retrieval_over_the_real_chunks_finds_the_right_one_for_a_clear_question():
    client = QdrantClient(":memory:")
    knowledge_base.setup(client, embed_fn=fake_embed)
    top = rag.retrieve("What happens to the delivery SLA on Black Friday and Christmas high-demand dates?", k=3, min_score=0.0, client=client, embed_fn=fake_embed)
    assert (top[0]["source_document"], top[0]["chunk_index"]) == ("sla-delivery", 2)


# ------------------------------------------------------------------ query()
@pytest.fixture
def chat(monkeypatch):
    """Mocks the generation model: records the request and returns a canned answer."""
    monkeypatch.setenv("LLM_MODEL", "chat-model")
    state = SimpleNamespace(requests=[], answer="Standard returns are accepted within 30 days from delivery.\nSource: returns-policy")

    def post(path, payload):
        state.requests.append((path, payload))
        return {"choices": [{"message": {"content": state.answer}}]}

    monkeypatch.setattr(model_client, "post", post)
    return state


CHUNK = {"source_document": "returns-policy", "section": "Returns Policy > Standard return window", "chunk_index": 1, "text": "Standard return window: 30 days from delivery.", "score": 0.9}


def test_query_returns_the_model_output_and_not_the_chunk(monkeypatch, chat):
    monkeypatch.setattr(rag, "retrieve", lambda question, **kw: [CHUNK])
    answer = rag.query("What is the standard return window?")
    assert answer == chat.answer
    assert answer != CHUNK["text"] and CHUNK["text"] not in answer  # the raw chunk is never what comes back


def test_query_is_retrieve_plus_generate_answer(monkeypatch):
    calls = []
    monkeypatch.setattr(rag, "retrieve", lambda question, **kw: calls.append(("retrieve", question)) or [CHUNK])
    monkeypatch.setattr(rag, "generate_answer", lambda question, context: calls.append(("generate", question, context)) or "the answer")
    assert rag.query("  a question ") == "the answer"
    assert calls == [("retrieve", "a question"), ("generate", "a question", [CHUNK])]


def test_the_generation_call_goes_to_the_chat_model_with_the_retrieved_context(monkeypatch, chat):
    monkeypatch.setenv("EMBEDDING_MODEL", "embed-model")
    monkeypatch.setattr(rag, "retrieve", lambda question, **kw: [CHUNK])
    rag.query("What is the standard return window?")
    path, payload = chat.requests[0]
    assert path == "/chat/completions" and payload["model"] == "chat-model" and payload["model"] != "embed-model"
    system, user = payload["messages"]
    assert system["role"] == "system" and user["role"] == "user"
    assert CHUNK["text"] in user["content"] and "returns-policy" in user["content"] and CHUNK["section"] in user["content"]
    assert "What is the standard return window?" in user["content"]


def test_with_nothing_above_the_threshold_the_model_is_still_asked_and_told_there_is_no_information(monkeypatch, chat):
    chat.answer = "I don't have that information in the knowledge base; please confirm it with the Commercial Director."
    monkeypatch.setattr(rag, "retrieve", lambda question, **kw: [])
    assert rag.query("What is the weather in Zaragoza?") == chat.answer
    user = chat.requests[0][1]["messages"][1]["content"]
    assert rag.NO_CONTEXT_NOTE in user and "[1]" not in user  # no chunk was put in the prompt, only the instruction to say so


def test_the_prompt_sets_the_voice_the_audience_and_the_business_constraints_of_the_context():
    prompt = rag.SYSTEM_PROMPT
    assert "account manager" in prompt and "salesperson" in prompt and "ONLY the context" in prompt
    assert "high-demand" in prompt and "Black Friday" in prompt  # no SLA promise on those dates
    assert 'never "automatic"' in prompt and "manual handling" in prompt  # international returns
    assert "Miguel Torres" in prompt and "Carlos Vega" in prompt  # approvals
    assert "no information" in prompt or "no information about it" in prompt


def test_an_empty_question_is_rejected_before_any_search(monkeypatch):
    monkeypatch.setattr(rag, "retrieve", lambda *a, **k: pytest.fail("must not search"))
    with pytest.raises(ValueError):
        rag.query("   ")


def test_an_empty_or_missing_model_answer_is_an_error_not_an_empty_string(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "chat-model")
    monkeypatch.setattr(model_client, "post", lambda path, payload: {"choices": [{"message": {"content": "  "}}]})
    with pytest.raises(model_client.ModelServiceError):
        rag.generate_answer("q", [CHUNK])
    monkeypatch.setattr(model_client, "post", lambda path, payload: {"unexpected": True})
    with pytest.raises(model_client.ModelServiceError):
        rag.generate_answer("q", [CHUNK])


def test_the_generation_model_must_be_configured(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(model_client.ModelServiceError):
        rag.generate_answer("q", [CHUNK])


def test_the_pipeline_entry_points_are_separate_functions():
    import inspect

    assert callable(knowledge_base.setup) and callable(embeddings.embed) and callable(rag.retrieve) and callable(rag.query) and callable(rag.generate_answer)
    assert "generate_answer" in inspect.getsource(rag.query) and "retrieve" in inspect.getsource(rag.query)
    assert "query_points" not in inspect.getsource(rag.query) and "post(" not in inspect.getsource(rag.query)  # query() only composes


# ------------------------------------------------------------------ the retrieval evaluation set
def _queries():
    import json

    return json.loads((config.ROOT / "data" / "eval" / "test-queries.json").read_text(encoding="utf-8"))


def test_the_query_file_has_at_least_eight_questions_covering_all_four_documents_and_a_peak_question():
    questions = _queries()["questions"]
    assert len(questions) >= 8
    assert {q["expected_source_document"] for q in questions} == set(config.SOURCE_DOCUMENTS.values())
    assert any("black friday" in q["question"].lower() or "sales" in q["question"].lower() for q in questions)  # CONTEXT section 5
    assert len({q["id"] for q in questions}) == len(questions)


def test_every_expected_chunk_of_the_query_file_exists_and_its_section_matches():
    chunks = {(c.source_document, c.chunk_index): c for c in rag_chunking.load_chunks()}
    for q in _queries()["questions"]:
        chunk = chunks[(q["expected_source_document"], q["expected_chunk_index"])]
        assert chunk.section == q["expected_section"], q["id"]


def test_the_evaluation_harness_computes_recall_at_3_and_a_threshold_recommendation():
    import importlib.util

    spec = importlib.util.spec_from_file_location("evaluate_rag", config.ROOT / "scripts" / "evaluate_rag.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = QdrantClient(":memory:")
    knowledge_base.setup(client, embed_fn=fake_embed)
    result = module.evaluate(_queries(), client=client, embed_fn=fake_embed)
    assert result["questions"] == len(_queries()["questions"]) and 0.0 <= result["recall_at_3"] <= 1.0
    assert all(len(row["top3"]) == 3 for row in result["per_question"])  # k=3, no threshold while ranking
    assert result["target"] == 0.8 and len(result["off_topic"]) == len(_queries()["off_topic"])
    assert all(row["hit_at_3"] == (row["rank_of_expected"] is not None) for row in result["per_question"])


# ------------------------------------------------------------------ .env for the scripts
def test_the_env_file_is_read_without_overriding_the_environment_and_skips_empty_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nLLM_API_URL=https://models.test/v1\nLLM_API_KEY="quoted-key"\nEMBEDDING_MODEL=\nLLM_MODEL=from-file\n', encoding="utf-8")
    for name in ("LLM_API_URL", "LLM_API_KEY", "EMBEDDING_MODEL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_MODEL", "already-set")
    loaded = config.load_env_file(env)
    assert sorted(loaded) == ["LLM_API_KEY", "LLM_API_URL"]  # the empty one and the one already set are left alone
    assert config.llm_api_url() == "https://models.test/v1" and config.llm_api_key() == "quoted-key"
    assert config.generation_model() == "already-set" and config.embedding_model() == ""
    monkeypatch.delenv("LLM_API_URL"), monkeypatch.delenv("LLM_API_KEY")  # the loader set them for real: clean up
    assert config.load_env_file(tmp_path / "missing.env") == []
