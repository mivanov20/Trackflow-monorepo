"""Retrieval evaluation of the TrackFlow knowledge base: Recall@3 and the minimum similarity score.

    python scripts/evaluate_rag.py [--setup] [--queries data/eval/test-queries.json] [--out data/eval/rag_eval_results.json]

For every question of data/eval/test-queries.json it embeds the question, takes the 3 best chunks from Qdrant (no threshold) and checks
whether the expected chunk is among them. It also scores the off-topic questions, which have no answer in the knowledge base, and
recommends a `RAG_MIN_SCORE` that keeps every correct top-1 chunk and rejects every off-topic question when such a value exists.
Exit code 1 if Recall@3 is under the target of the context (80%).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.pipelines import rag  # noqa: E402
from data.process import knowledge_base  # noqa: E402

DEFAULT_QUERIES = ROOT / "data" / "eval" / "test-queries.json"
DEFAULT_OUT = ROOT / "data" / "eval" / "rag_eval_results.json"


def evaluate(queries: dict, client=None, embed_fn=None, k: int = 3) -> dict:
    kwargs = {"client": client} if client is not None else {}
    if embed_fn is not None:
        kwargs["embed_fn"] = embed_fn
    rows = []
    for item in queries["questions"]:
        results = rag.retrieve(item["question"], k=k, min_score=-1.0, **kwargs)  # min_score -1: keep everything, only rank
        ranked = [(r["source_document"], r["chunk_index"]) for r in results]
        expected = (item["expected_source_document"], item["expected_chunk_index"])
        rank = ranked.index(expected) + 1 if expected in ranked else None
        rows.append({"id": item["id"], "question": item["question"], "expected": list(expected), "rank_of_expected": rank, "hit_at_3": rank is not None, "top_score": results[0]["score"] if results else None, "expected_score": next((r["score"] for r in results if (r["source_document"], r["chunk_index"]) == expected), None), "top3": [{"source_document": r["source_document"], "chunk_index": r["chunk_index"], "score": round(r["score"], 4)} for r in results]})
    off_topic = []
    for question in queries.get("off_topic", []):
        results = rag.retrieve(question, k=1, min_score=-1.0, **kwargs)
        off_topic.append({"question": question, "top_score": results[0]["score"] if results else None})
    hits = sum(r["hit_at_3"] for r in rows)
    lowest_correct = min((r["expected_score"] for r in rows if r["expected_score"] is not None and r["rank_of_expected"] == 1), default=None)
    highest_off_topic = max((o["top_score"] for o in off_topic if o["top_score"] is not None), default=None)
    recommendation = None
    if lowest_correct is not None and highest_off_topic is not None:
        recommendation = {"lowest_score_of_a_correct_top1": lowest_correct, "highest_score_of_an_off_topic_question": highest_off_topic, "separable": lowest_correct > highest_off_topic, "recommended_min_score": round((lowest_correct + highest_off_topic) / 2, 3) if lowest_correct > highest_off_topic else None}
    return {"questions": len(rows), "recall_at_3": hits / len(rows), "hits": hits, "target": queries.get("recall_target", 0.8), "per_question": rows, "off_topic": off_topic, "min_score_analysis": recommendation}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queries", default=str(DEFAULT_QUERIES))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--setup", action="store_true", help="index the documents first")
    args = parser.parse_args(argv)
    if args.setup:
        print("setup:", json.dumps(knowledge_base.setup()))
    result = evaluate(json.loads(Path(args.queries).read_text(encoding="utf-8")))
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    for row in result["per_question"]:
        print(f"  {'OK ' if row['hit_at_3'] else 'MISS'} rank={row['rank_of_expected']}  {row['id']}  (top score {row['top_score']:.3f})")
    print(f"Recall@3 = {result['recall_at_3']:.0%} ({result['hits']}/{result['questions']}), target {result['target']:.0%}")
    if result["min_score_analysis"]:
        print("min_score analysis:", json.dumps(result["min_score_analysis"]))
    return 0 if result["recall_at_3"] >= result["target"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
