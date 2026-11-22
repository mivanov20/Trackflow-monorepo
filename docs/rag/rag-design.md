# RAG design: TrackFlow knowledge base (milestone 7)

An account manager types a question ("what's the standard return window?", "which carrier best covers rural Aragón?") and gets an answer written in the voice of a TrackFlow salesperson, with exact data and never promising terms that are not in the standard agreements. The answer is **always written by a generation model from the retrieved context**; the result of the vector search is never returned.

No orchestration framework (LangChain, LlamaIndex...): the Qdrant SDK, `httpx` and FastAPI, and four functions written by hand.

## 1. The process, end to end

```
docs/company-knowledge-base/*.md                       (4 documents of CONTEXT-company.md)
        |  setup()   data/process/knowledge_base.py
        v
  rag_chunking.load_chunks()   -> 14 semantic chunks (3 + 5 + 3 + 3), each with its metadata
        |  embed(chunk)        data/process/embeddings.py       (EMBEDDINGS model)
        v
  Qdrant collection `trackflow_knowledge`   (cosine, deterministic point ids, payload = metadata + text)

  ------------------------------ at question time ------------------------------

POST /knowledge/query {"question": "..."}   services/routers/knowledge.py   (auth, validation, no logic)
        |  query(question)     data/pipelines/rag.py
        v
  retrieve(question)  -> embed(question) -> Qdrant top-k -> drop hits below min_score -> list[dict]   (0..k chunks)
        v
  generate_answer(question, context)  -> prompt = rules + chunks with their source headers + question
        |                                   -> GENERATION model (chat)
        v
  {"answer": "..."}    the model's text only: no chunks, no scores
```

| Function | File | One responsibility |
|---|---|---|
| `setup()` | `data/process/knowledge_base.py` | documents -> chunks -> vectors -> Qdrant (idempotent) |
| `embed(text)` | `data/process/embeddings.py` | one text -> one vector, embeddings model |
| `retrieve(query, *, k, min_score)` | `data/pipelines/rag.py` | question -> the chunks that clear the threshold |
| `generate_answer(question, context)` | `data/pipelines/rag.py` | question + chunks -> answer, generation model |
| `query(question)` | `data/pipelines/rag.py` | `retrieve()` + `generate_answer()`; the only entry point for consumers |

`query()` is literally the two calls, so the later LangGraph agent can call `retrieve()` and `generate_answer()` as separate steps without running retrieval twice. Any piece can be swapped (another vector store, another prompt, another model service) without touching the others: the endpoint only knows `query()`, `query()` only knows the other two, and the HTTP calls to the models live in one small file (`data/process/model_client.py`).

The endpoint never duplicates retrieval or generation logic (a test reads the router's source and fails if it mentions Qdrant, `embed(`, `chat/completions` or `min_score`). Search results (sources, scores) are logged on the server by `query()` for debugging and never sent to the client. If the model service, the embeddings model or Qdrant fails, the client gets a 503 with a generic message: no key, address or question in it.

## 2. Chunking strategy

**Strategy: one chunk per semantic block, found by structure, not by size.** The four documents are short (about 1 KB each) and have a single `#` title, so splitting by heading would give one chunk per document, and a fixed number of characters would cut rules in half. The unit used is the *block* (the text between blank lines), which in these documents is exactly one idea. Two rules keep every chunk self-contained:

- A block that ends with ":" is an introduction and joins the block that follows (a list never separates from its lead sentence).
- A numbered process (steps 1-3) and a bulleted list stay whole.

| Document (`source_document`) | Chunks | What each one holds | Size (characters) |
|---|---|---|---|
| `sla-delivery` | 3 | service types and delivery times; the 90% SLA and the 5% compensation; the high-demand warning (Black Friday, Christmas, January Sales, +40%) | 248-549 |
| `returns-policy` | 5 | the 3-step process; the 30-day window; return costs and when TrackFlow pays; international returns (manual handling); what TrackFlow does not do | 123-435 |
| `carrier-coverage` | 3 | United States carriers; Spain carriers (SEUR rural Aragón, MRW, local carriers); how the carrier is chosen and who approves exceptions | 256-344 |
| `storage-pricing` | 3 | the standard rates, the 30-day grace period and the long-term fee; the aging report; the preferential rate (Miguel Torres) | 167-454 |

Why it fits this corpus: every business rule keeps its condition in the same chunk (the 90% SLA with its compensation; "international returns are not covered by the automated process" with "manual handling"; the storage discount with "must go through Miguel Torres"). That matters because the constraints of the context depend on those conditions: a chunk with the discount but without the approval would make the model offer a discount. A test checks that the chunks, joined, are exactly the text of the document (nothing lost, nothing cut in two), that no chunk ends mid-sentence and that no introduction is left alone.

**Metadata.** Each point stores `company`, `source_document`, `section`, `language`, `chunk_index` and `text` (the exact fields of CONTEXT, plus `text` for the prompt). The documents have no subheadings, so `section` is the document title plus a short subtitle (`rag_config.SECTION_TITLES`, for example `Returns Policy > International returns (manual handling)`); a chunk without an entry gets a label derived from its own text. The prompt gives the model each chunk with its source and section so the answer can end with a `Source:` line: the answer is traceable to its document.

**What is embedded.** The text sent to the embeddings model is `<document title> - <section>` plus the chunk. A 123-character chunk such as "Standard return window: 30 days from delivery..." loses its context when it is cut out of its document, and the title and section give it back. The stored `text` payload stays the plain chunk body.

## 3. Embedding practices

- **Two models, two jobs.** `EMBEDDING_MODEL` (`embed()`, vectors) and `LLM_MODEL` (`generate_answer()`, chat completions) are configured separately and must differ: `embed()` refuses to run if they are the same id. Both are reached through the model service of the academy (`LLM_API_URL`, `LLM_API_KEY`), a third-party model offered to students (such as DeepSeek or a GPT model), over an OpenAI-compatible API (`/embeddings`, `/chat/completions`). **The model ids and the key are not committed**: they are read from the environment (`.env.example` lists the variables). *Status: the ids used in the tests are mocks; the real ids are filled in `.env` by whoever runs the stack, and the measurements of section 4 are produced with them.*
- **One `embed()` for both sides.** The same function embeds the chunks at index time and the question at query time, with the same preprocessing (whitespace collapsed, case and punctuation kept: nothing else), so both vectors live in the same space. A test checks that `retrieve()` embeds the question with `embed()`.
- **Vector size and distance.** The size is not hard coded: `setup()` takes it from the first vector the model returns, and creates the collection with it and with **cosine** distance (the metric the embeddings models are trained for; the score is a cosine similarity, which is what `min_score` compares against). If the model changes and the size no longer matches, `setup()` recreates the collection, because vectors of different models cannot be mixed.
- **Idempotent `setup()`: deterministic point ids.** Each id is a `uuid5` of `trackflow/<source_document>/<chunk_index>`: running `setup()` again overwrites the same points instead of duplicating them, and points whose chunk no longer exists (a document got shorter) are deleted. Chosen over clear-and-reload because it never leaves the collection empty while re-indexing, and all vectors are computed before Qdrant is touched, so a model failure changes nothing (both tested, against an in-memory Qdrant and against a real Qdrant container: 14 points after the first run and after the second).
- **Retrieval and the threshold.** `retrieve()` asks Qdrant for the top `k` (default 5) and **then** drops every hit under `min_score`, so it can return fewer than `k`, or none: forcing a bad match into the prompt is worse than saying nothing was found. With nothing left, `generate_answer()` is still called, with an instruction to say that the knowledge base has no information and who should confirm it; the model never gets a chance to answer from general knowledge.
- **`min_score` and how it is tuned.** The default is `0.35` (`RAG_MIN_SCORE`) and is **a starting value, not a tuned one**: the right value depends on the embeddings model, and a cosine score of 0.35 means very different things for different models. `scripts/evaluate_rag.py` does the tuning with the real model: it records the score of the correct chunk of each question in `data/eval/test-queries.json` and the best score of each *off-topic* question (weather, football, a recipe...), and recommends the midpoint between the lowest score of a correct top-1 chunk and the highest score of an off-topic question when the two are separable. A value above the first would reject real questions; below the second, it would let irrelevant context into the prompt. After running it, set `RAG_MIN_SCORE` to the recommendation and record it here.
- **Generation.** Temperature 0, a system prompt that sets the voice (a TrackFlow salesperson helping an account manager on a call with a client), the sources rule (only the context; if it does not answer, say so; numbers copied exactly) and the business constraints of the context: no delivery SLA promised on declared high-demand dates, international returns never "automatic" (manual handling), no storage discount without Miguel Torres's approval, carrier exceptions need Carlos Vega's approval, and a condition that is not documented "requires approval", never invented.

## 4. Evaluation

`data/eval/test-queries.json` has 12 questions (3 for returns, 3 for delivery SLA including the Black Friday one, 3 for carriers, 3 for storage), each with the single chunk that answers it, and 6 off-topic questions. `python scripts/evaluate_rag.py --setup` indexes, computes **Recall@3** (the share of questions whose correct chunk is among the 3 best results; the target of the context is 80%) and the `min_score` recommendation, writes `data/eval/rag_eval_results.json` and exits with an error if the target is missed.

**Status of this measurement:** Recall@3 and the tuned threshold require the real embeddings model, which was not available when this was written, so **they have not been measured** and no number is claimed here. What is verified without it: the file covers the four documents, every expected chunk exists, and the harness computes the metric (tested with a toy embedder, which proves the mechanics, not the quality). Run the command with the model configured and add the result and the chosen `RAG_MIN_SCORE` below.

| Result | Value |
|---|---|
| Embeddings model / generation model | *to fill after the run* |
| Recall@3 | *to fill after the run (target 80%)* |
| `RAG_MIN_SCORE` chosen | *to fill after the run (default 0.35 until then)* |

## 5. Running it

```powershell
docker compose up -d qdrant                     # Qdrant on :6333 (QDRANT_URL=http://localhost:6333 outside Docker)
# put LLM_API_URL, LLM_API_KEY, EMBEDDING_MODEL, LLM_MODEL in .env
python scripts/index_knowledge_base.py           # setup(): safe to run again
python scripts/evaluate_rag.py                   # Recall@3 and the min_score recommendation
docker compose up -d api ui                      # POST /knowledge/query, page: /backoffice/knowledge ("Ask TrackFlow")
python -m pytest tests/pipelines/test_rag.py     # unit tests: mocks only, no Qdrant and no model needed
```

## 6. Limits

- The knowledge base is four short documents. A question outside them (a discount, a carrier exception, anything undocumented) is answered with "no information / requires approval", by design.
- Retrieval quality and the faithfulness of the answers depend on the models behind the service; the prompt and the temperature reduce the risk, they do not remove it: faithfulness (no percentage, rate or timeframe different from the chunks) should be spot checked with the real models.
- The endpoint requires a signed-in user and answers one question at a time; there is no conversational memory (RAG is a knowledge base the system consults, not memory of the conversation).
