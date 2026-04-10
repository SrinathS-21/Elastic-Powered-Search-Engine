from __future__ import annotations

from fastapi import APIRouter, Query

try:
    from ..services.benchmark import benchmark_query_set, run_quality_benchmark
except ImportError:
    from services.benchmark import benchmark_query_set, run_quality_benchmark

router = APIRouter()


@router.get("/quality/benchmark")
def quality_benchmark(
    query_set: str = Query(default="default", pattern="^(default|compact)$"),
    modes: str = Query(default="keyword,semantic,hybrid"),
    top_n: int = Query(default=3, ge=1, le=10),
    relevance_threshold: float = Query(default=0.5, ge=0.0, le=1.0),
):
    selected_queries = benchmark_query_set(query_set)
    selected_modes = [mode.strip() for mode in modes.split(",") if mode.strip()]

    return run_quality_benchmark(
        queries=selected_queries,
        modes=selected_modes,
        top_n=int(top_n),
        relevance_threshold=float(relevance_threshold),
    )
