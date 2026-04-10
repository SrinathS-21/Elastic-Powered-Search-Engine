from __future__ import annotations

try:
    from ...core.clients import es
    from ...core.config import INDEX_NAME
    from .common import as_text, sample_hierarchy_cards
    from ..synonyms import expand_synonyms, synonym_source_tokens, token_coverage, tokenize_for_match
except ImportError:
    from core.clients import es
    from core.config import INDEX_NAME
    from services.internal.common import as_text, sample_hierarchy_cards
    from services.synonyms import expand_synonyms, synonym_source_tokens, token_coverage, tokenize_for_match


def fetch_hierarchy_cards(keyword: str, max_cards: int = 3) -> tuple[list[dict], int]:
    query = keyword.strip()
    if not query:
        return [], 0

    expanded_query = expand_synonyms(query) or query
    query_lower = query.lower()
    expanded_lower = expanded_query.lower()

    try:
        if not es.indices.exists(index=INDEX_NAME):
            return sample_hierarchy_cards(query, max_cards=max_cards), 0

        should_clauses: list[dict] = [
            {"match_phrase": {"productName": {"query": query, "boost": 14.0}}},
            {"match_phrase_prefix": {"productName": {"query": query, "boost": 11.0}}},
            {
                "multi_match": {
                    "query": query,
                    "type": "bool_prefix",
                    "fields": [
                        "productName_autocomplete",
                        "productName_autocomplete._2gram",
                        "productName_autocomplete._3gram",
                    ],
                    "boost": 8.2,
                }
            },
            {"match": {"productName": {"query": query, "operator": "and", "boost": 7.0}}},
            {"match": {"productName.stem": {"query": query, "operator": "and", "boost": 5.2}}},
            {"match": {"productName.ngram": {"query": query, "operator": "and", "boost": 4.5}}},
            {"match": {"search_text": {"query": query, "operator": "and", "boost": 3.0}}},
            {"match": {"search_text.stem": {"query": query, "operator": "and", "boost": 2.4}}},
            {"match": {"suggest_text": {"query": query, "operator": "and", "boost": 3.4}}},
            {"match": {"suggest_text.stem": {"query": query, "operator": "and", "boost": 2.6}}},
            {"match": {"productName": {"query": query, "fuzziness": "AUTO", "prefix_length": 1, "boost": 0.9}}},
            {"match": {"productDescription": {"query": query, "operator": "or", "boost": 0.15}}},
            {"match": {"productDescription.stem": {"query": query, "operator": "or", "boost": 0.2}}},
        ]

        if expanded_lower != query_lower:
            should_clauses.extend(
                [
                    {"match_phrase": {"productName": {"query": expanded_query, "boost": 13.0}}},
                    {"match_phrase_prefix": {"productName": {"query": expanded_query, "boost": 10.0}}},
                    {
                        "multi_match": {
                            "query": expanded_query,
                            "type": "bool_prefix",
                            "fields": [
                                "productName_autocomplete",
                                "productName_autocomplete._2gram",
                                "productName_autocomplete._3gram",
                            ],
                            "boost": 8.8,
                        }
                    },
                    {"match": {"productName": {"query": expanded_query, "operator": "and", "boost": 8.0}}},
                    {"match": {"productName.stem": {"query": expanded_query, "operator": "and", "boost": 5.6}}},
                    {"match": {"productName.ngram": {"query": expanded_query, "operator": "and", "boost": 5.0}}},
                    {"match": {"search_text": {"query": expanded_query, "operator": "and", "boost": 3.3}}},
                    {"match": {"search_text.stem": {"query": expanded_query, "operator": "and", "boost": 2.7}}},
                    {"match": {"suggest_text": {"query": expanded_query, "operator": "and", "boost": 3.6}}},
                    {"match": {"suggest_text.stem": {"query": expanded_query, "operator": "and", "boost": 2.8}}},
                    {"match": {"productName": {"query": expanded_query, "fuzziness": "AUTO", "prefix_length": 1, "boost": 1.0}}},
                    {"match": {"productDescription": {"query": expanded_query, "operator": "or", "boost": 0.2}}},
                    {"match": {"productDescription.stem": {"query": expanded_query, "operator": "or", "boost": 0.24}}},
                ]
            )

        response = es.search(
            index=INDEX_NAME,
            size=260,
            _source=["productName", "productDescription", "category_name", "subCategory_name", "productCategory_name"],
            query={
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                }
            },
        )

        hits = response.get("hits", {}).get("hits", [])
        stats: dict[str, dict] = {}
        query_tokens = tokenize_for_match(query)
        expanded_tokens = tokenize_for_match(expanded_query)
        source_tokens = synonym_source_tokens()
        anchor_tokens = {token for token in query_tokens if token not in source_tokens}
        min_coverage = 0.5 if len(query_tokens) >= 2 else 1.0

        for hit in hits:
            source = hit.get("_source", {})
            product_name = as_text(source.get("productName"))
            if not product_name:
                continue
            product_description = as_text(source.get("productDescription"))
            product_name_lower = product_name.lower()
            name_tokens = tokenize_for_match(product_name)

            if anchor_tokens and not (anchor_tokens & name_tokens):
                continue

            is_exact_phrase = (query_lower in product_name_lower) or (
                expanded_lower != query_lower and expanded_lower in product_name_lower
            )
            is_prefix = product_name_lower.startswith(query_lower) or (
                expanded_lower != query_lower and product_name_lower.startswith(expanded_lower)
            )
            is_token_and = (query_tokens and query_tokens.issubset(name_tokens)) or (
                expanded_tokens and expanded_tokens.issubset(name_tokens)
            )

            name_coverage = max(token_coverage(query, product_name), token_coverage(expanded_query, product_name))
            desc_coverage = max(token_coverage(query, product_description), token_coverage(expanded_query, product_description))
            blended_coverage = max(name_coverage, desc_coverage * 0.6)

            passes_filter = is_exact_phrase or is_prefix or is_token_and or (blended_coverage >= min_coverage)
            if not passes_filter:
                continue

            if is_exact_phrase:
                rank_weight = 4
            elif is_prefix:
                rank_weight = 3
            elif is_token_and:
                rank_weight = 2
            else:
                rank_weight = 1

            category_name = as_text(source.get("category_name"))
            subcategory_name = as_text(source.get("subCategory_name"))
            product_category_name = as_text(source.get("productCategory_name"))
            breadcrumb = " >> ".join([part for part in [category_name, subcategory_name, product_category_name] if part])
            if not breadcrumb:
                continue

            bucket = stats.setdefault(
                breadcrumb,
                {
                    "count": 0,
                    "score_sum": 0.0,
                    "weight_sum": 0,
                    "coverage_sum": 0.0,
                    "exact_hits": 0,
                    "prefix_hits": 0,
                    "token_and_hits": 0,
                    "semantic_hits": 0,
                    "sample_products": [],
                },
            )
            bucket["count"] += 1
            bucket["score_sum"] += float(hit.get("_score") or 0.0)
            bucket["weight_sum"] += rank_weight
            bucket["coverage_sum"] += blended_coverage

            if is_exact_phrase:
                bucket["exact_hits"] += 1
            elif is_prefix:
                bucket["prefix_hits"] += 1
            elif is_token_and:
                bucket["token_and_hits"] += 1
            else:
                bucket["semantic_hits"] += 1

            if product_name and product_name not in bucket["sample_products"] and len(bucket["sample_products"]) < 2:
                bucket["sample_products"].append(product_name)

        if not stats:
            return sample_hierarchy_cards(query, max_cards=max_cards), len(hits)

        ranked = sorted(
            stats.items(),
            key=lambda kv: (
                kv[1]["exact_hits"],
                kv[1]["prefix_hits"],
                kv[1]["token_and_hits"],
                (kv[1]["coverage_sum"] / kv[1]["count"]) if kv[1]["count"] else 0.0,
                kv[1]["weight_sum"],
                kv[1]["count"],
                kv[1]["score_sum"],
            ),
            reverse=True,
        )
        total_weights = sum(item[1]["weight_sum"] for item in ranked) or 1

        strong_ranked = [
            pair for pair in ranked
            if (pair[1]["exact_hits"] + pair[1]["prefix_hits"] + pair[1]["token_and_hits"]) > 0
        ]
        selected_ranked = strong_ranked[:max_cards] if len(strong_ranked) >= max_cards else ranked[:max_cards]

        cards: list[dict] = []
        for breadcrumb, item in selected_ranked:
            avg_coverage = (item["coverage_sum"] / item["count"]) if item["count"] else 0.0
            cards.append(
                {
                    "breadcrumb": breadcrumb,
                    "count": int(item["count"]),
                    "correlation_pct": round(item["weight_sum"] / total_weights * 100, 1),
                    "avg_token_coverage": round(avg_coverage, 3),
                    "ranking_basis": {
                        "exact_hits": int(item["exact_hits"]),
                        "prefix_hits": int(item["prefix_hits"]),
                        "token_and_hits": int(item["token_and_hits"]),
                        "semantic_hits": int(item["semantic_hits"]),
                    },
                    "sample_products": item["sample_products"],
                }
            )
        return cards, len(hits)
    except Exception:
        return sample_hierarchy_cards(query, max_cards=max_cards), 0
