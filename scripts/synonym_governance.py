from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _normalize(value: Any) -> str:
    return "" if value is None else str(value).strip().lower()


def _split_rule(rule_text: str) -> tuple[str, str] | None:
    parts = [_normalize(part) for part in str(rule_text).split(",")]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return None
    left, right = parts[0], parts[1]
    if left == right:
        return None
    return left, right


def _parse_synonym_payload(payload: Any) -> dict[str, str]:
    synonym_map: dict[str, str] = {}

    if isinstance(payload, dict):
        for source, target in payload.items():
            left = _normalize(source)
            right = _normalize(target)
            if not left or not right or left == right:
                continue
            synonym_map[left] = right
        return synonym_map

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                split = _split_rule(item)
                if not split:
                    continue
                left, right = split
                synonym_map[left] = right
                continue

            if isinstance(item, dict):
                left = _normalize(
                    item.get("source")
                    or item.get("term")
                    or item.get("abbr")
                    or item.get("from")
                    or item.get("left")
                )
                right = _normalize(
                    item.get("target")
                    or item.get("canonical")
                    or item.get("expansion")
                    or item.get("to")
                    or item.get("right")
                )
                if not left or not right or left == right:
                    continue
                synonym_map[left] = right

    return synonym_map


def _load_synonyms_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        for key in ("synonyms", "synonym_map", "items", "rules", "synonym_rules"):
            parsed = _parse_synonym_payload(payload.get(key))
            if parsed:
                return parsed
        parsed = _parse_synonym_payload(payload)
        if parsed:
            return parsed

    return _parse_synonym_payload(payload)


def _validate_synonym_map(synonym_map: dict[str, str]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    for source, target in sorted(synonym_map.items()):
        if source == target:
            errors.append(f"self_mapping:{source}")
        if len(source) < 2:
            errors.append(f"short_source:{source}")
        if len(target) < 2:
            warnings.append(f"short_target:{source}->{target}")
        if len(target.split()) > 6:
            warnings.append(f"long_expansion:{source}->{target}")

    # Detect cycles and deep chains.
    for source in sorted(synonym_map.keys()):
        seen: list[str] = [source]
        curr = source
        depth = 0
        while curr in synonym_map:
            nxt = synonym_map[curr]
            depth += 1
            if nxt in seen:
                cycle = " -> ".join(seen + [nxt])
                errors.append(f"cycle:{cycle}")
                break
            seen.append(nxt)
            curr = nxt
            if depth > 10:
                warnings.append(f"deep_chain:{source}")
                break
        if depth >= 2:
            warnings.append(f"chain_depth:{source}:{depth}")

    # Bidirectional swap conflicts.
    for source, target in sorted(synonym_map.items()):
        if synonym_map.get(target) == source:
            errors.append(f"bidirectional_conflict:{source}<->{target}")

    dedup_errors = sorted(set(errors))
    dedup_warnings = sorted(set(warnings))

    return {
        "errors": dedup_errors,
        "warnings": dedup_warnings,
        "error_count": len(dedup_errors),
        "warning_count": len(dedup_warnings),
    }


def _load_proposal(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Proposal file must be a JSON object")

    add = payload.get("add") or {}
    replace = payload.get("replace") or {}
    remove = payload.get("remove") or []

    if not isinstance(add, dict) or not isinstance(replace, dict) or not isinstance(remove, list):
        raise ValueError("Proposal file must include add(dict), replace(dict), remove(list)")

    clean_add = {_normalize(k): _normalize(v) for k, v in add.items() if _normalize(k) and _normalize(v)}
    clean_replace = {_normalize(k): _normalize(v) for k, v in replace.items() if _normalize(k) and _normalize(v)}
    clean_remove = [_normalize(item) for item in remove if _normalize(item)]

    return {
        "add": clean_add,
        "replace": clean_replace,
        "remove": clean_remove,
    }


def _apply_proposal(base: dict[str, str], proposal: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    result = dict(base)

    removed = 0
    for token in proposal["remove"]:
        if token in result:
            del result[token]
            removed += 1

    replaced = 0
    for token, expansion in proposal["replace"].items():
        if token in result and result[token] != expansion:
            replaced += 1
        result[token] = expansion

    added = 0
    for token, expansion in proposal["add"].items():
        if token not in result:
            added += 1
        result[token] = expansion

    return result, {
        "added": added,
        "removed": removed,
        "replaced": replaced,
    }


def _write_synonyms(path: Path, synonym_map: dict[str, str]) -> None:
    payload = {
        "synonyms": {
            key: synonym_map[key]
            for key in sorted(synonym_map.keys())
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _snapshot(path: Path, history_dir: Path) -> Path:
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = history_dir / f"synonyms_{timestamp}.json"
    if path.exists():
        snapshot_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        snapshot_path.write_text(json.dumps({"synonyms": {}}, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return snapshot_path


def _latest_snapshot(history_dir: Path) -> Path | None:
    if not history_dir.exists():
        return None
    snapshots = sorted(history_dir.glob("synonyms_*.json"))
    return snapshots[-1] if snapshots else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synonym governance workflow (validate/review/apply/rollback)")
    parser.add_argument("command", choices=["validate", "snapshot", "review-proposal", "apply-proposal", "rollback"])
    parser.add_argument("--synonyms", default="resources/synonyms.json")
    parser.add_argument("--proposal", default="")
    parser.add_argument("--history-dir", default="resources/synonyms_history")
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--output", choices=["summary", "full"], default="summary")
    parser.add_argument("--write-report", default="")
    return parser.parse_args()


def _emit(report: dict[str, Any], write_report: str = "") -> None:
    if write_report:
        output_path = Path(write_report)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=True))


def main() -> None:
    args = parse_args()
    synonyms_path = Path(args.synonyms)
    history_dir = Path(args.history_dir)

    if args.command == "validate":
        synonym_map = _load_synonyms_file(synonyms_path)
        validation = _validate_synonym_map(synonym_map)
        report = {
            "command": args.command,
            "synonyms": str(synonyms_path),
            "count": len(synonym_map),
            **validation,
        }
        _emit(report, write_report=args.write_report)
        raise SystemExit(1 if validation["error_count"] else 0)

    if args.command == "snapshot":
        snapshot_path = _snapshot(synonyms_path, history_dir)
        report = {
            "command": args.command,
            "synonyms": str(synonyms_path),
            "snapshot": str(snapshot_path),
        }
        _emit(report, write_report=args.write_report)
        return

    if args.command in {"review-proposal", "apply-proposal"}:
        if not args.proposal:
            raise SystemExit("--proposal is required for review-proposal/apply-proposal")
        proposal_path = Path(args.proposal)
        base_map = _load_synonyms_file(synonyms_path)
        proposal = _load_proposal(proposal_path)
        merged_map, delta = _apply_proposal(base_map, proposal)
        validation = _validate_synonym_map(merged_map)

        report = {
            "command": args.command,
            "synonyms": str(synonyms_path),
            "proposal": str(proposal_path),
            "before_count": len(base_map),
            "after_count": len(merged_map),
            "delta": delta,
            **validation,
        }

        if args.command == "review-proposal":
            if args.output == "summary":
                report.pop("warnings", None)
                report.pop("errors", None)
            _emit(report, write_report=args.write_report)
            raise SystemExit(1 if validation["error_count"] else 0)

        if validation["error_count"]:
            _emit(report, write_report=args.write_report)
            raise SystemExit(1)

        snapshot_path = _snapshot(synonyms_path, history_dir)
        _write_synonyms(synonyms_path, merged_map)
        report["applied"] = True
        report["snapshot"] = str(snapshot_path)
        _emit(report, write_report=args.write_report)
        return

    if args.command == "rollback":
        if args.snapshot:
            source = Path(args.snapshot)
        else:
            latest = _latest_snapshot(history_dir)
            if latest is None:
                report = {
                    "command": args.command,
                    "synonyms": str(synonyms_path),
                    "error": "No snapshot found",
                }
                _emit(report, write_report=args.write_report)
                raise SystemExit(1)
            source = latest

        if not source.exists():
            report = {
                "command": args.command,
                "synonyms": str(synonyms_path),
                "error": f"Snapshot not found: {source}",
            }
            _emit(report, write_report=args.write_report)
            raise SystemExit(1)

        synonyms_path.parent.mkdir(parents=True, exist_ok=True)
        synonyms_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        rolled_map = _load_synonyms_file(synonyms_path)
        validation = _validate_synonym_map(rolled_map)

        report = {
            "command": args.command,
            "synonyms": str(synonyms_path),
            "snapshot": str(source),
            "count": len(rolled_map),
            **validation,
        }
        _emit(report, write_report=args.write_report)
        raise SystemExit(1 if validation["error_count"] else 0)


if __name__ == "__main__":
    main()
