FROM python:3.13-alpine
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy
# add venv to path
ENV PATH="/app/.venv/bin:$PATH"

COPY controller.py /app/controller.py
RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev

CMD ["kopf", "run", "--standalone", "--verbose", "controller.py"]
