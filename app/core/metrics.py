"""Prometheus metrics collection."""

from prometheus_client import Counter, Histogram

# HTTP request metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Database metrics
db_queries_total = Counter(
    "db_queries_total",
    "Total number of database queries",
    ["operation"],
)

db_query_duration_seconds = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

# Ingestion metrics
ingestion_runs_total = Counter(
    "ingestion_runs_total",
    "Total number of ingestion runs",
    ["provider", "status"],
)

ingestion_duration_seconds = Histogram(
    "ingestion_duration_seconds",
    "Ingestion duration in seconds",
    ["provider"],
)
