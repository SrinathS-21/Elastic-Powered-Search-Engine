from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.internal.suggestions import fetch_keyword_suggestions
from src.services.mapping import map_query_to_categories


SITUATION_CASES: list[dict[str, Any]] = [
    {
        "name": "exact_intent",
        "query": "stainless steel pipe",
        "expected_any": ["pipe"],
        "banned_any": [],
        "suggestion_anchors": ["pipe"],
    },
    {
        "name": "abbreviation_intent",
        "query": "ms channel",
        "expected_any": ["channel"],
        "banned_any": [],
        "suggestion_anchors": ["channel"],
    },
    {
        "name": "demographic_intent",
        "query": "leather shoe for women",
        "expected_any": ["shoe", "footwear"],
        "banned_any": ["handbag"],
        "suggestion_anchors": ["shoe", "women"],
    },
    {
        "name": "plural_variant",
        "query": "leather shoes for women",
        "expected_any": ["shoe", "footwear"],
        "banned_any": ["handbag"],
        "suggestion_anchors": ["shoe", "women"],
    },
    {
        "name": "typo_tolerance",
        "query": "industrial valv",
        "expected_any": ["valve"],
        "banned_any": [],
        "suggestion_anchors": ["valve"],
    },
    {
        "name": "short_ambiguous_query",
        "query": "shoe",
        "expected_any": [],
        "banned_any": [],
        "suggestion_anchors": ["shoe"],
    },
    {
        "name": "selected_suggestion_flow",
        "query": "leather shoe for women",
        "selected_suggestion": "leather shoes for women",
        "expected_any": ["shoe", "footwear"],
        "banned_any": ["handbag"],
        "suggestion_anchors": ["shoe", "women"],
    },
    {
        "name": "out_of_domain_safe",
        "query": "quantum flux capacitor",
        "expected_any": [],
        "banned_any": [],
        "suggestion_anchors": [],
    },
]


def _contains_any(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    query = str(case.get("query") or "").strip()
    selected_suggestion = case.get("selected_suggestion")

    first = map_query_to_categories(query, selected_suggestion=selected_suggestion, max_cards=3)
    second = map_query_to_categories(query, selected_suggestion=selected_suggestion, max_cards=3)

    top1 = first.get("top_category") or {}
    top2 = second.get("top_category") or {}

    breadcrumb = str(top1.get("breadcrumb") or "")
    top_id_1 = str(top1.get("product_category_id") or "")
    top_id_2 = str(top2.get("product_category_id") or "")

    suggestions = fetch_keyword_suggestions(query, limit=8)

    checks: list[dict[str, Any]] = []

    has_top = bool(top_id_1)
    checks.append({"name": "has_top_category", "ok": has_top, "severity": "critical"})

    deterministic = (top_id_1 == top_id_2)
    checks.append({"name": "deterministic_top", "ok": deterministic, "severity": "critical"})

    expected_any = [str(item).lower() for item in (case.get("expected_any") or [])]
    if expected_any:
        checks.append(
            {
                "name": "expected_any_match",
                "ok": _contains_any(breadcrumb, expected_any),
                "severity": "major",
            }
        )

    banned_any = [str(item).lower() for item in (case.get("banned_any") or [])]
    if banned_any:
        checks.append(
            {
                "name": "banned_any_absent",
                "ok": not _contains_any(breadcrumb, banned_any),
                "severity": "major",
            }
        )

    anchors = [str(item).lower() for item in (case.get("suggestion_anchors") or [])]
    if anchors:
        suggestion_anchor_ok = any(all(anchor in suggestion.lower() for anchor in anchors) for suggestion in suggestions)
        checks.append(
            {
                "name": "suggestion_anchor_present",
                "ok": suggestion_anchor_ok,
                "severity": "minor",
            }
        )

    decision = str(first.get("decision") or "")
    checks.append(
        {
            "name": "valid_decision",
            "ok": decision in {"auto_map", "confirm", "options"},
            "severity": "critical",
        }
    )

    critical_failures = [item for item in checks if not item["ok"] and item["severity"] == "critical"]
    major_failures = [item for item in checks if not item["ok"] and item["severity"] == "major"]
    minor_failures = [item for item in checks if not item["ok"] and item["severity"] == "minor"]

    return {
        "name": case.get("name"),
        "query": query,
        "selected_suggestion": selected_suggestion,
        "decision": decision,
        "top_category_id": top_id_1,
        "top_breadcrumb": breadcrumb,
        "confidence": float(first.get("confidence") or 0.0),
        "lanes_used": first.get("lanes_used") or [],
        "alerts": first.get("alerts") or [],
        "checks": checks,
        "critical_failures": len(critical_failures),
        "major_failures": len(major_failures),
        "minor_failures": len(minor_failures),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate user situation handling for mapping + suggestions")
    parser.add_argument("--output", choices=["summary", "full"], default="summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    results = [_run_case(case) for case in SITUATION_CASES]
    critical_failures = sum(item["critical_failures"] for item in results)
    major_failures = sum(item["major_failures"] for item in results)
    minor_failures = sum(item["minor_failures"] for item in results)

    report: dict[str, Any] = {
        "cases": len(results),
        "critical_failures": critical_failures,
        "major_failures": major_failures,
        "minor_failures": minor_failures,
        "overall_status": "pass" if critical_failures == 0 else "fail",
    }

    if args.output == "full":
        report["results"] = results

    print(json.dumps(report, indent=2, ensure_ascii=True))

    raise SystemExit(1 if critical_failures > 0 else 0)


if __name__ == "__main__":
    main()
