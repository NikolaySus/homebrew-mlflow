FROM python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /uvx /bin/
WORKDIR /app
ENV UV_NO_DEV=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package homebrew-mlflow-plugins
COPY build/mlflow-ui/ /app/.venv/lib/python3.11/site-packages/mlflow/server/js/build/
USER 65532:65532
EXPOSE 5000
CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000", "--workers", "2", "--backend-store-uri", "homebrew://platform", "--workspace-store-uri", "homebrew://platform", "--enable-workspaces", "--no-serve-artifacts"]
