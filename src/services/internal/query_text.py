from __future__ import annotations

from functools import lru_cache
from typing import Any

try:
    from nltk.stem import SnowballStemmer, WordNetLemmatizer
except Exception:
    SnowballStemmer = None
    WordNetLemmatizer = None

try:
    from ...core.config import PHRASE_CANDIDATE_LIMIT
    from ...core.synonym_data import load_protected_tokens
    from .common import as_text
except ImportError:
    from core.config import PHRASE_CANDIDATE_LIMIT
    from core.synonym_data import load_protected_tokens
    from services.internal.common import as_text

# Common connective words that often create low-signal keyword permutations.
QUERY_NOISE_TOKENS = {
    "for", "with", "without", "and", "or", "to", "from", "in", "on", "of", "by", "at",
}

# Keep dynamically configured short synonym source tokens intact during stemming.
CANONICAL_SKIP_TOKENS = frozenset(load_protected_tokens())

_SNOWBALL_STEMMER = SnowballStemmer("english") if SnowballStemmer is not None else None
_WORDNET_LEMMATIZER = WordNetLemmatizer() if WordNetLemmatizer is not None else None


def token_list(text: str) -> list[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return [t for t in cleaned.split() if len(t) >= 2]


@lru_cache(maxsize=4096)
def canonical_token(token: str) -> str:
    value = as_text(token).lower().strip()
    if len(value) < 2:
        return value
    if value in CANONICAL_SKIP_TOKENS:
        return value

    canonical = value
    if _WORDNET_LEMMATIZER is not None:
        try:
            canonical = _WORDNET_LEMMATIZER.lemmatize(canonical, "n")
            canonical = _WORDNET_LEMMATIZER.lemmatize(canonical, "v")
        except LookupError:
            canonical = value
        except Exception:
            canonical = value

    if _SNOWBALL_STEMMER is not None:
        try:
            canonical = _SNOWBALL_STEMMER.stem(canonical)
        except Exception:
            pass

    return canonical or value


def canonical_tokens(tokens: list[str]) -> list[str]:
    return [canonical_token(token) for token in tokens if token]


def canonical_token_list(text: str) -> list[str]:
    return canonical_tokens(token_list(text))


def significant_tokens(tokens: list[str]) -> list[str]:
    return [tok for tok in tokens if tok not in QUERY_NOISE_TOKENS]


def normalize_query_text(text: str) -> str:
    return " ".join(token_list(text))


def build_phrase_candidates(tokens: list[str], max_candidates: int = PHRASE_CANDIDATE_LIMIT) -> list[str]:
    if len(tokens) < 2:
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    max_n = min(4, len(tokens))
    for n in range(max_n, 1, -1):
        for idx in range(0, len(tokens) - n + 1):
            phrase = " ".join(tokens[idx : idx + n])
            if phrase in seen:
                continue
            seen.add(phrase)
            candidates.append(phrase)
            if len(candidates) >= max_candidates:
                return candidates
    return candidates


def build_query_context(text: str) -> dict[str, Any]:
    raw_tokens = token_list(text)
    normalized_query = " ".join(raw_tokens)
    intent_tokens = significant_tokens(raw_tokens)
    if not intent_tokens:
        intent_tokens = raw_tokens[:]

    intent_query = " ".join(intent_tokens)
    phrase_candidates = build_phrase_candidates(intent_tokens)
    anchor_tokens = intent_tokens[-2:] if len(intent_tokens) >= 2 else intent_tokens[:]
    canonical_raw_tokens = canonical_tokens(raw_tokens)
    canonical_intent_tokens = canonical_tokens(intent_tokens)
    return {
        "raw_tokens": raw_tokens,
        "normalized_query": normalized_query,
        "intent_tokens": intent_tokens,
        "canonical_raw_tokens": canonical_raw_tokens,
        "canonical_intent_tokens": canonical_intent_tokens,
        "intent_query": intent_query,
        "phrase_candidates": phrase_candidates,
        "anchor_tokens": anchor_tokens,
        "ends_with_noise": bool(raw_tokens and raw_tokens[-1] in QUERY_NOISE_TOKENS),
    }


def find_contiguous_sublist(haystack: list[str], needle: list[str]) -> int:
    if not needle or len(needle) > len(haystack):
        return -1
    n = len(needle)
    for idx in range(len(haystack) - n + 1):
        if haystack[idx:idx + n] == needle:
            return idx
    return -1


def is_subsequence(haystack: list[str], needle: list[str]) -> bool:
    if not needle:
        return False
    j = 0
    for tok in haystack:
        if j < len(needle) and tok == needle[j]:
            j += 1
            if j == len(needle):
                return True
    return False


def suggestion_rank_features(term: str, query: str) -> dict[str, int]:
    term_l = term.lower().strip()
    q_l = query.lower().strip()

    term_tokens = canonical_token_list(term_l)
    q_tokens = canonical_token_list(q_l)
    q_token_set = set(q_tokens)
    term_token_set = set(term_tokens)

    if not q_l:
        stage = 9
    elif term_l == q_l:
        stage = 0
    elif term_l.startswith(q_l):
        stage = 1
    elif q_tokens and q_token_set.issubset(term_token_set):
        contiguous_idx = find_contiguous_sublist(term_tokens, q_tokens)
        if contiguous_idx == 0:
            stage = 2
        elif contiguous_idx > 0:
            stage = 3
        elif is_subsequence(term_tokens, q_tokens):
            stage = 4
        else:
            stage = 5
    elif q_l and q_l in term_l:
        stage = 6
    else:
        stage = 7

    first_q = q_tokens[0] if q_tokens else ""
    first_pos = term_tokens.index(first_q) if first_q and first_q in term_tokens else 99
    first_token_mismatch = 0 if (not q_tokens or (term_tokens and term_tokens[0] == q_tokens[0])) else 1
    starts_numeric = 1 if (term_l[:1].isdigit()) else 0
    length_delta = abs(len(term_tokens) - len(q_tokens)) if q_tokens else len(term_tokens)
    token_coverage = int(round((len(q_token_set & term_token_set) / max(1, len(q_token_set))) * 100)) if q_tokens else 0

    return {
        "stage": stage,
        "first_pos": first_pos,
        "first_token_mismatch": first_token_mismatch,
        "starts_numeric": starts_numeric,
        "length_delta": length_delta,
        "token_coverage": token_coverage,
    }


def is_noisy_suggestion_term(text: str) -> bool:
    tokens = token_list(text)
    if not tokens:
        return True

    raw_tokens = [tok for tok in "".join(ch.lower() if ch.isalnum() else " " for ch in as_text(text)).split() if tok]
    if raw_tokens and len(raw_tokens[-1]) == 1 and raw_tokens[-1].isalpha():
        return True

    if len(tokens) >= 2 and tokens[-1] in QUERY_NOISE_TOKENS:
        return True
    if len(tokens) >= 2 and tokens[0] in QUERY_NOISE_TOKENS:
        return True
    return False


def has_strong_term_evidence(term: str, normalized_query: str, query_tokens: list[str]) -> bool:
    term_value = as_text(term).lower()
    if not term_value or not normalized_query:
        return False
    if term_value.startswith(normalized_query) or normalized_query in term_value:
        return True

    sig_q_tokens = canonical_tokens(significant_tokens(query_tokens))
    if not sig_q_tokens:
        return False

    term_tokens = canonical_token_list(term_value)
    if not term_tokens:
        return False

    overlap = len(set(sig_q_tokens) & set(term_tokens))
    if overlap == len(sig_q_tokens):
        return True

    return overlap >= 2 and term_tokens[0].startswith(sig_q_tokens[0])
