FROM python:3.13-alpine
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

RUN addgroup --system app && adduser --system --ingroup app app

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev

COPY controller.py /app/controller.py

RUN chown -R app:app /app
USER app

CMD ["kopf", "run", "--standalone", "--verbose", "controller.py"]
