"""Search quality benchmarking service.

Measures search performance across multiple dimensions: latency, relevance scores,
token coverage, and query-category mapping accuracy.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from src.services.search import search_products
from src.services.synonyms import COMPACT_BENCHMARK_QUERIES, DEFAULT_BENCHMARK_QUERIES, percentile, token_coverage


def run_quality_benchmark(
    queries: list[str],
    modes: list[str],
    top_n: int = 3,
    relevance_threshold: float = 0.5,
) -> dict:
    allowed_modes = {"keyword", "semantic", "hybrid"}
    clean_modes = [mode for mode in modes if mode in allowed_modes]
    if not clean_modes:
        clean_modes = ["keyword", "semantic", "hybrid"]

    rows: list[dict] = []

    for mode in clean_modes:
        for query in queries:
            try:
                result = search_products(
                    query=query,
                    page=1,
                    category=None,
                    sub_category=None,
                    prod_category=None,
                    mode=mode,
                )
                hits = result.get("hits", [])
                top_hits = hits[: max(1, top_n)]

                top1_coverage = 0.0
                topn_max_coverage = 0.0
                if top_hits:
                    top1_coverage = token_coverage(query, top_hits[0].get("productName", ""))
                    topn_max_coverage = max(
                        (token_coverage(query, hit.get("productName", "")) for hit in top_hits),
                        default=0.0,
                    )

                rows.append(
                    {
                        "query": query,
                        "mode": mode,
                        "total_hits": int(result.get("total", 0)),
                        "latency_ms": float(result.get("latency_ms", 0.0)),
                        "top1_confidence": float(top_hits[0].get("confidence", 0.0)) if top_hits else 0.0,
                        "top1_token_coverage": round(top1_coverage, 4),
                        "topn_max_token_coverage": round(topn_max_coverage, 4),
                        "relevance_pass": bool(topn_max_coverage >= relevance_threshold),
                        "error": "",
                    }
                )
            except HTTPException as exc:
                rows.append(
                    {
                        "query": query,
                        "mode": mode,
                        "total_hits": 0,
                        "latency_ms": 0.0,
                        "top1_confidence": 0.0,
                        "top1_token_coverage": 0.0,
                        "topn_max_token_coverage": 0.0,
                        "relevance_pass": False,
                        "error": str(exc.detail),
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "query": query,
                        "mode": mode,
                        "total_hits": 0,
                        "latency_ms": 0.0,
                        "top1_confidence": 0.0,
                        "top1_token_coverage": 0.0,
                        "topn_max_token_coverage": 0.0,
                        "relevance_pass": False,
                        "error": str(exc),
                    }
                )

    mode_summary: dict[str, dict] = {}
    for mode in clean_modes:
        mode_rows = [row for row in rows if row["mode"] == mode]
        ok_rows = [row for row in mode_rows if not row["error"]]
        latencies = [row["latency_ms"] for row in ok_rows]
        hit_queries = [row for row in ok_rows if row["total_hits"] > 0]
        relevance_passes = [row for row in ok_rows if row["relevance_pass"]]

        mode_summary[mode] = {
            "queries": len(mode_rows),
            "ok_queries": len(ok_rows),
            "error_queries": len(mode_rows) - len(ok_rows),
            "hit_rate_pct": round((len(hit_queries) / len(ok_rows) * 100) if ok_rows else 0.0, 2),
            "relevance_pass_rate_pct": round((len(relevance_passes) / len(ok_rows) * 100) if ok_rows else 0.0, 2),
            "avg_latency_ms": round((sum(latencies) / len(latencies)) if latencies else 0.0, 2),
            "p95_latency_ms": round(percentile(latencies, 95), 2),
            "avg_top1_confidence": round(
                (sum(row["top1_confidence"] for row in ok_rows) / len(ok_rows)) if ok_rows else 0.0,
                2,
            ),
        }

    all_ok = [row for row in rows if not row["error"]]
    all_latencies = [row["latency_ms"] for row in all_ok]
    overall = {
        "rows": len(rows),
        "ok_rows": len(all_ok),
        "error_rows": len(rows) - len(all_ok),
        "avg_latency_ms": round((sum(all_latencies) / len(all_latencies)) if all_latencies else 0.0, 2),
        "p95_latency_ms": round(percentile(all_latencies, 95), 2),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queries": queries,
        "modes": clean_modes,
        "top_n": int(top_n),
        "relevance_threshold": float(relevance_threshold),
        "summary_by_mode": mode_summary,
        "overall": overall,
        "rows": rows,
    }


def benchmark_query_set(query_set: str) -> list[str]:
    return DEFAULT_BENCHMARK_QUERIES if query_set == "default" else COMPACT_BENCHMARK_QUERIES
