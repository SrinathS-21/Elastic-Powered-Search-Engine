from __future__ import annotations

from elasticsearch import Elasticsearch

from .config import ES_HOST

es = Elasticsearch(ES_HOST, request_timeout=10, retry_on_timeout=True)
