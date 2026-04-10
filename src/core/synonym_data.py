from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _resolve_synonym_file() -> Path | None:
    configured = os.getenv("B2B_SYNONYMS_FILE", "").strip()
    if not configured:
        return None

    file_path = Path(configured)
    if not file_path.is_absolute():
        base_dir = Path(__file__).resolve().parent.parent.parent
        file_path = (base_dir / file_path).resolve()
    return file_path


def _split_rule(rule_text: str) -> tuple[str, str] | None:
    if not rule_text:
        return None
    parts = [_normalize(part) for part in str(rule_text).split(",")]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return None
    left = parts[0]
    right = parts[1]
    if left == right:
        return None
    return left, right


def _parse_payload(payload: Any, synonym_map: dict[str, str], rules: list[str]) -> None:
    if isinstance(payload, dict):
        for source, target in payload.items():
            left = _normalize(source)
            right = _normalize(target)
            if not left or not right or left == right:
                continue
            synonym_map[left] = right
        return

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                split = _split_rule(item)
                if not split:
                    continue
                left, right = split
                synonym_map[left] = right
                rules.append(f"{left}, {right}")
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
                rules.append(f"{left}, {right}")


@lru_cache(maxsize=1)
def _load_synonym_data() -> tuple[dict[str, str], list[str]]:
    synonym_file = _resolve_synonym_file()
    if synonym_file is None or not synonym_file.exists() or not synonym_file.is_file():
        return {}, []

    try:
        raw_text = synonym_file.read_text(encoding="utf-8")
    except Exception:
        return {}, []

    if not raw_text.strip():
        return {}, []

    try:
        data = json.loads(raw_text)
    except Exception:
        return {}, []

    synonym_map: dict[str, str] = {}
    explicit_rules: list[str] = []

    if isinstance(data, dict):
        _parse_payload(data.get("synonyms"), synonym_map, explicit_rules)
        _parse_payload(data.get("synonym_map"), synonym_map, explicit_rules)
        _parse_payload(data.get("items"), synonym_map, explicit_rules)
        _parse_payload(data.get("rules"), synonym_map, explicit_rules)
        _parse_payload(data.get("synonym_rules"), synonym_map, explicit_rules)

        if not synonym_map and not explicit_rules:
            _parse_payload(data, synonym_map, explicit_rules)
    else:
        _parse_payload(data, synonym_map, explicit_rules)

    if not explicit_rules:
        explicit_rules = [f"{source}, {target}" for source, target in synonym_map.items()]

    deduped_rules: list[str] = []
    seen_rules: set[str] = set()
    for rule in explicit_rules:
        split = _split_rule(rule)
        if not split:
            continue
        left, right = split
        normalized_rule = f"{left}, {right}"
        if normalized_rule in seen_rules:
            continue
        seen_rules.add(normalized_rule)
        deduped_rules.append(normalized_rule)

    return synonym_map, deduped_rules


def load_synonym_map() -> dict[str, str]:
    synonym_map, _ = _load_synonym_data()
    return dict(synonym_map)


def load_synonym_rules() -> list[str]:
    _, rules = _load_synonym_data()
    return list(rules)


def load_protected_tokens(max_len: int = 6) -> set[str]:
    synonym_map, _ = _load_synonym_data()
    protected: set[str] = set()
    for token in synonym_map.keys():
        if " " in token:
            continue
        if 2 <= len(token) <= max_len:
            protected.add(token)
    return protected
