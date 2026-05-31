FROM postgres:18

ARG PG_SEARCH_VERSION=0.23.4
ARG PG_SEARCH_DISTRO=trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -L "https://github.com/paradedb/paradedb/releases/download/v${PG_SEARCH_VERSION}/postgresql-18-pg-search_${PG_SEARCH_VERSION}-1PARADEDB-${PG_SEARCH_DISTRO}_amd64.deb" -o /tmp/pg_search.deb \
    && apt-get install -y --no-install-recommends /tmp/pg_search.deb \
    && rm -f /tmp/pg_search.deb \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*
