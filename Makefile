.PHONY: help setup corpus ingest poison eval attack bench report test lint clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup:  ## Installe l'environnement (uv + Python 3.12)
	uv sync --extra dev

corpus:  ## Télécharge le corpus (documentation française de FastAPI)
	./scripts/fetch_corpus.sh

ingest:  ## Indexe le corpus propre, le corpus empoisonné et sa version nettoyée
	uv run rpib ingest --collection clean
	uv run rpib poison
	uv run rpib ingest --corpus corpus/poisoned --collection poisoned
	uv run rpib ingest --corpus corpus/poisoned --collection poisoned_d2 --sanitize

eval:  ## Évalue la récupération puis la génération
	uv run rpib eval-retrieval --detail
	uv run rpib eval-utility --condition e2e

attack:  ## Mesure l'ASR sans défense
	uv run rpib attack --collection poisoned

bench:  ## Ablation complète : ASR et exactitude pour chaque configuration
	uv run rpib bench

report:  ## Produit results/report.md et les figures
	uv run rpib report

test:  ## Tests unitaires
	uv run pytest -q

lint:  ## Vérification statique
	uv run ruff check src/ tests/

clean:  ## Supprime index et résultats bruts
	rm -rf chroma_db results/runs
