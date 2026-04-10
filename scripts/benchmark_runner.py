from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.main import app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run search quality benchmark via TestClient")
    parser.add_argument("--query-set", default="compact", choices=["default", "compact"])
    parser.add_argument("--modes", default="keyword,semantic,hybrid")
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--relevance-threshold", type=float, default=0.5)
    parser.add_argument("--output", default="summary", choices=["summary", "full"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    client = TestClient(app)
    response = client.get(
        "/quality/benchmark",
        params={
            "query_set": args.query_set,
            "modes": args.modes,
            "top_n": args.top_n,
            "relevance_threshold": args.relevance_threshold,
        },
    )
    response.raise_for_status()

    data = response.json()
    if args.output == "full":
        print(json.dumps(data, indent=2, ensure_ascii=True))
        return

    summary = {
        "query_set": data.get("queries", []),
        "modes": data.get("modes", []),
        "overall": data.get("overall", {}),
        "summary_by_mode": data.get("summary_by_mode", {}),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
