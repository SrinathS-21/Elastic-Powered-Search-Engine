from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.internal.suggestions import fetch_keyword_suggestions
from src.services.mapping import map_query_to_categories


CRITICAL_CASES = [
    {
        "query": "leather shoe for women",
        "expected_any": ["footwear", "shoe"],
        "banned_any": ["handbag"],
        "suggestion_anchors": ["shoe", "women"],
    },
    {
        "query": "leather shoes for women",
        "expected_any": ["footwear", "shoe"],
        "banned_any": ["handbag"],
        "suggestion_anchors": ["shoe", "women"],
    },
    {
        "query": "ms channel",
        "expected_any": ["channel"],
        "banned_any": [],
        "suggestion_anchors": ["channel"],
    },
    {
        "query": "industrial valve",
        "expected_any": ["valve"],
        "banned_any": [],
        "suggestion_anchors": ["valve"],
    },
    {
        "query": "steel pipe for water",
        "expected_any": ["pipe"],
        "banned_any": [],
        "suggestion_anchors": ["pipe"],
    },
]

PAIR_STABILITY_CASES = [
    ("leather shoe for women", "leather shoes for women"),
    ("leather shoe for men", "leather shoes for men"),
    ("steel pipe for water", "steel pipes for water"),
    ("industrial valve", "industrial valves"),
    ("ms channel", "ms channels"),
]

MATERIALS = ["leather", "steel", "stainless", "cotton", "industrial", "premium", "heavy duty"]
PRODUCTS = ["shoes", "shoe", "pipe", "pipes", "valve", "valves", "channel", "channels", "shirt", "shirts"]
SEGMENTS = ["women", "men", "kids", "water", "industry", "construction", "safety"]


def _contains_any(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def _top_category_payload(query: str) -> dict[str, Any]:
    payload = map_query_to_categories(query, selected_suggestion=None, max_cards=3)
    top = payload.get("top_category") or {}
    return {
        "query": query,
        "decision": payload.get("decision"),
        "confidence": float(payload.get("confidence") or 0.0),
        "top_id": top.get("product_category_id") or "",
        "top_breadcrumb": top.get("breadcrumb") or "",
        "lanes_used": payload.get("lanes_used") or [],
        "semantic_used": bool(payload.get("semantic_used")),
        "product_fallback_used": bool(payload.get("product_fallback_used")),
    }


def _run_critical_checks() -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for case in CRITICAL_CASES:
        query = case["query"]
        payload = _top_category_payload(query)
        top_breadcrumb = str(payload["top_breadcrumb"]) if payload["top_breadcrumb"] else ""

        mapping_payload = map_query_to_categories(query, selected_suggestion=None, max_cards=3)
        cards = mapping_payload.get("cards") or []

        if case["expected_any"] and not _contains_any(top_breadcrumb, case["expected_any"]):
            failures.append(
                {
                    "type": "critical_top_mismatch",
                    "query": query,
                    "expected_any": case["expected_any"],
                    "actual_top": top_breadcrumb,
                    "decision": payload["decision"],
                    "confidence": payload["confidence"],
                }
            )

        if case["banned_any"] and _contains_any(top_breadcrumb, case["banned_any"]):
            failures.append(
                {
                    "type": "critical_top_banned",
                    "query": query,
                    "banned_any": case["banned_any"],
                    "actual_top": top_breadcrumb,
                    "decision": payload["decision"],
                    "confidence": payload["confidence"],
                }
            )

        if case["expected_any"] and _contains_any(top_breadcrumb, case["expected_any"]):
            if float(payload["confidence"] or 0.0) < 0.30:
                failures.append(
                    {
                        "type": "critical_low_confidence",
                        "query": query,
                        "actual_top": top_breadcrumb,
                        "confidence": payload["confidence"],
                    }
                )

        if len(cards) >= 2:
            top = cards[0]
            second = cards[1]
            top_basis = top.get("ranking_basis") or {}
            second_basis = second.get("ranking_basis") or {}
            top_lex_sem = int(top_basis.get("lexical_cluster_hits") or 0) + int(top_basis.get("semantic_cluster_hits") or 0)
            second_lex_sem = int(second_basis.get("lexical_cluster_hits") or 0) + int(second_basis.get("semantic_cluster_hits") or 0)
            top_cov = float(top.get("avg_token_coverage") or 0.0)
            second_cov = float(second.get("avg_token_coverage") or 0.0)
            top_corr = float(top.get("correlation_pct") or 0.0)
            second_corr = float(second.get("correlation_pct") or 0.0)

            if (
                top_lex_sem + 4 <= second_lex_sem
                and top_cov + 0.15 <= second_cov
                and top_corr > second_corr
            ):
                failures.append(
                    {
                        "type": "top2_consistency_conflict",
                        "query": query,
                        "top": {
                            "breadcrumb": top.get("breadcrumb"),
                            "corr": top_corr,
                            "coverage": top_cov,
                            "lex_sem_hits": top_lex_sem,
                        },
                        "second": {
                            "breadcrumb": second.get("breadcrumb"),
                            "corr": second_corr,
                            "coverage": second_cov,
                            "lex_sem_hits": second_lex_sem,
                        },
                    }
                )

        suggestions = fetch_keyword_suggestions(query, limit=8)
        if case["suggestion_anchors"]:
            if not any(
                all(anchor in suggestion.lower() for anchor in case["suggestion_anchors"])
                for suggestion in suggestions
            ):
                failures.append(
                    {
                        "type": "suggestion_anchor_missing",
                        "query": query,
                        "anchors": case["suggestion_anchors"],
                        "suggestions": suggestions,
                    }
                )

    return failures


def _run_pair_stability_checks() -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for singular, plural in PAIR_STABILITY_CASES:
        left = _top_category_payload(singular)
        right = _top_category_payload(plural)

        if not left["top_id"] or not right["top_id"]:
            continue

        if left["top_id"] != right["top_id"]:
            min_confidence = min(float(left["confidence"]), float(right["confidence"]))
            if min_confidence >= 0.15:
                failures.append(
                    {
                        "type": "pair_flip",
                        "queries": [singular, plural],
                        "left_top": left["top_breadcrumb"],
                        "right_top": right["top_breadcrumb"],
                        "left_conf": left["confidence"],
                        "right_conf": right["confidence"],
                    }
                )

    return failures


def _random_query() -> str:
    material = random.choice(MATERIALS)
    product = random.choice(PRODUCTS)
    segment = random.choice(SEGMENTS)
    pattern = random.choice(
        [
            "{material} {product} for {segment}",
            "{material} {product} {segment}",
            "{segment} {material} {product}",
        ]
    )
    return pattern.format(material=material, product=product, segment=segment)


def _run_random_determinism_checks(samples: int) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    seen: set[str] = set()

    while len(seen) < samples:
        seen.add(_random_query())

    for query in sorted(seen):
        left = _top_category_payload(query)
        right = _top_category_payload(query)

        if left["top_id"] and right["top_id"] and left["top_id"] != right["top_id"]:
            failures.append(
                {
                    "type": "nondeterministic_top",
                    "query": query,
                    "left_top": left["top_breadcrumb"],
                    "right_top": right["top_breadcrumb"],
                }
            )

    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run relevance regression checks for category mapping and suggestions")
    parser.add_argument("--random-samples", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", choices=["summary", "full"], default="summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.seed)

    critical_failures = _run_critical_checks()
    pair_failures = _run_pair_stability_checks()
    random_failures = _run_random_determinism_checks(samples=max(5, int(args.random_samples)))

    failures = critical_failures + pair_failures + random_failures
    report = {
        "critical_checks": len(CRITICAL_CASES),
        "pair_checks": len(PAIR_STABILITY_CASES),
        "random_checks": max(5, int(args.random_samples)),
        "failure_count": len(failures),
        "failures": failures,
    }

    if args.output == "full":
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        summary = {
            "critical_checks": report["critical_checks"],
            "pair_checks": report["pair_checks"],
            "random_checks": report["random_checks"],
            "failure_count": report["failure_count"],
        }
        print(json.dumps(summary, indent=2, ensure_ascii=True))
        if failures:
            print("\nFirst 5 failures:")
            print(json.dumps(failures[:5], indent=2, ensure_ascii=True))

    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
