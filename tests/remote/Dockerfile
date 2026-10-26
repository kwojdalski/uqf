# A machine of its own for the remote-stack lane (scripts/test.py remote-stack).
#
# Linux x86_64, as the servers a stack runs on are, with PeachQ built from the
# pin (scripts/peachq.json): there is no Linux KDB-X here, and PeachQ needs no
# licence. The checkout is COPIED in, last, rather than bind-mounted: the point
# of the container is a machine that shares no filesystem with the one driving
# it, so a path that happens to exist on both can never make a check pass. A
# rebuild after an edit redoes only that last layer.
#
# The build context is the working tree's files as git sees them (tracked and
# untracked-not-ignored), streamed by the lane, so .venv, output/ and caches
# never enter it.
FROM --platform=linux/amd64 debian:bookworm-slim

# gcc/make/git build PeachQ; gettext-base (envsubst) and rlwrap are what
# torq.sh needs, and uqs's prerequisite check refuses a start without them;
# procps for ps, which uqs reads to find its processes; lsof, which torq.sh
# stops a process with.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates git make gcc g++ libc6-dev gettext-base rlwrap procps \
        iproute2 netcat-openbsd lsof \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /usr/local/bin/

ENV XDG_CACHE_HOME=/opt/cache \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    UV_NO_DEV=1 \
    UV_PYTHON=cpython-3.14-linux-x86_64-gnu

# Named in full: under Rosetta uv can take the machine for aarch64.
RUN uv python install "$UV_PYTHON"

# PeachQ from the pin, into the resolver's own cache: the layer is reused until
# the pin or the builder changes.
WORKDIR /uqf
COPY scripts/peachq.py scripts/peachq.json scripts/
RUN uv run --no-project scripts/peachq.py

# uqs's dependencies, before the tree, so an edit to the tree reuses them.
COPY pyproject.toml uv.lock ./
COPY python/uqs/pyproject.toml python/uqs/README.md python/uqs/
COPY python/uqf_frontend/pyproject.toml python/uqf_frontend/
COPY python/uqf_airflow_provider/pyproject.toml python/uqf_airflow_provider/README.md python/uqf_airflow_provider/
RUN uv sync --package uqs --no-install-workspace

COPY . /uqf
# The one q of this machine is PeachQ, said out loud: QCMD names it and
# UQF_Q_IMPL declares it, which is what `uqs job receive` starts its receiver
# with. Every runtime's data lives under /data.
RUN uv sync --package uqs \
    && ln -s /opt/venv/bin/uqs /usr/local/bin/uqs \
    && ln -s "$(uv run --no-project scripts/peachq.py)" /usr/local/bin/peachq \
    && mkdir -p /data
ENV QCMD=/usr/local/bin/peachq \
    UQF_Q_IMPL=peachq \
    UQS_DATA_ROOT=/data

# Nothing runs until the lane says so: it starts the stack with `docker exec`,
# so a failed start is reported by the test that asked for it.
CMD ["sleep", "infinity"]
