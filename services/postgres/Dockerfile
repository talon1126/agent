FROM postgres:18

ARG PG_SEARCH_VERSION=0.23.4
ARG PG_SEARCH_DISTRO=trixie
ARG APT_RETRIES=5

# Install both PostgreSQL extensions required by the shared application
# database. pg_search powers existing full-text workflows, while pgvector
# provides the `vector` type and HNSW operators used by the RAG subsystem.
# GitHub release downloads can reset long-lived HTTP/2 streams, so curl uses
# HTTP/1.1 and retries transient transport failures.
RUN apt-get -o Acquire::Retries=${APT_RETRIES} update \
    && apt-get -o Acquire::Retries=${APT_RETRIES} install -y --no-install-recommends ca-certificates curl \
    && curl --fail --location --http1.1 --retry 5 --retry-all-errors --retry-delay 2 \
        "https://github.com/paradedb/paradedb/releases/download/v${PG_SEARCH_VERSION}/postgresql-18-pg-search_${PG_SEARCH_VERSION}-1PARADEDB-${PG_SEARCH_DISTRO}_amd64.deb" \
        --output /tmp/pg_search.deb \
    && apt-get -o Acquire::Retries=${APT_RETRIES} install -y --no-install-recommends /tmp/pg_search.deb \
    && apt-get -o Acquire::Retries=${APT_RETRIES} install -y --no-install-recommends postgresql-18-pgvector \
    && rm -f /tmp/pg_search.deb \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*
