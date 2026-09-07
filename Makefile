.PHONY: help setup corpus ingest eval attack bench report all sweep test lint clean

help:  ## Affiche cette aide
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-9s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------------- #
# Préparation
# --------------------------------------------------------------------------- #

setup:  ## Installe l'environnement Python 3.12 (~2 min)
	uv sync --extra dev

corpus:  ## Télécharge le corpus, doc française de FastAPI (~10 s)
	./scripts/fetch_corpus.sh

ingest:  ## Construit les 4 index vectoriels (~4 min, aucun appel au LLM)
	uv run rpib ingest --collection clean
	uv run rpib ingest --corpus corpus/clean --collection clean_d2 --sanitize
	uv run rpib poison
	uv run rpib ingest --corpus corpus/poisoned --collection poisoned
	uv run rpib ingest --corpus corpus/poisoned --collection poisoned_d2 --sanitize

# --------------------------------------------------------------------------- #
# Mesures
# --------------------------------------------------------------------------- #

eval:  ## Recherche puis génération (~10 min ; la recherche seule est instantanée)
	uv run rpib eval-retrieval --detail
	uv run rpib eval-utility --condition oracle
	uv run rpib eval-utility --condition e2e
	uv run rpib eval-utility --condition closed

sweep:  ## Balaie taille de passage x recouvrement (~10 min, aucun appel au LLM)
	uv run rpib sweep

attack:  ## Mesure l'ASR sans défense (~20 min)
	uv run rpib attack --collection poisoned --prompt-style naive

bench:  ## Ablation complète : ASR ET exactitude par configuration (~5 h sur CPU)
	uv run rpib bench

report:  ## Produit results/report.md et les figures (instantané)
	uv run rpib report

all: corpus ingest bench report  ## Chaîne complète, de zéro au rapport (~5 h)

# --------------------------------------------------------------------------- #
# Qualité
# --------------------------------------------------------------------------- #

test:  ## Tests unitaires (aucun modèle requis)
	uv run pytest -q

lint:  ## Vérification statique
	uv run ruff check src/ tests/

clean:  ## Supprime les index et les résultats bruts
	rm -rf chroma_db results/runs
