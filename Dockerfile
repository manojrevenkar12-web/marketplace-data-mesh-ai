# The platform's API and CLI (ECS Fargate in infra/terraform/api.tf). The console is a static build served
# separately; data lives on the mounted lake (locally) or in S3.
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MESH_ROOT=/app
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && useradd --uid 10001 --create-home mesh

COPY config ./config
COPY products ./products
COPY reports ./reports
COPY heldout ./heldout
COPY prompts.lock ./
RUN mkdir -p /app/data && chown mesh /app/data

USER mesh
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["mesh", "serve", "--host", "0.0.0.0", "--port", "8080"]
