# Construction en deux étapes : l'étape de dépendances embarque le compilateur et
# les caches de uv, l'étape finale repart d'une base propre et ne reçoit que
# l'environnement virtuel. L'image finale ne contient ni uv, ni cache, ni outillage
# de développement.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS deps

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY src ./src
# --no-dev : pytest et ruff n'ont rien à faire dans une image d'exécution.
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# Le modèle d'embedding est téléchargé À LA CONSTRUCTION, pas au premier appel :
# une image qui a besoin du réseau pour démarrer n'est pas reproductible.
RUN uv run python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('intfloat/multilingual-e5-small')"


FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

# Utilisateur non privilégié : une injection de prompt réussie ne doit pas
# rencontrer un processus root de l'autre côté.
RUN useradd --create-home --uid 10001 rpib
WORKDIR /app

COPY --from=deps /app/.venv /app/.venv
COPY --from=deps /root/.cache/huggingface /home/rpib/.cache/huggingface
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/rpib/.cache/huggingface \
    OLLAMA_HOST=http://ollama:11434 \
    CHROMA_PATH=/app/chroma_db

COPY src ./src
COPY data ./data
COPY scripts ./scripts
COPY pyproject.toml README.md ./
# Les repertoires de donnees sont ouverts en ecriture a tout uid, et le cache du
# modele lisible par tous. Raison : docker-compose fait tourner le conteneur sous
# l'uid de l'hote pour que les fichiers ecrits dans le montage lie lui
# appartiennent. L'image, elle, reste non privilegiee par defaut (USER rpib), ce
# que verifie l'integration continue.
RUN mkdir -p corpus/clean corpus/poisoned results/runs chroma_db \
    && chown -R rpib:rpib /app /home/rpib \
    && chmod -R 777 /app/corpus /app/results /app/chroma_db \
    && chmod -R a+rX /home/rpib/.cache

USER rpib
ENTRYPOINT ["rpib"]
CMD ["--help"]
