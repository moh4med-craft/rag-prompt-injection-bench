#!/usr/bin/env bash
# Récupère le corpus : documentation française de FastAPI.
# Corpus public, technique, en français — reproductible par quiconque clone ce dépôt.
set -euo pipefail

DEST="corpus/clean"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Récupération de la documentation FastAPI (fr)..."
git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/fastapi/fastapi.git "$TMP/fastapi" >/dev/null 2>&1
git -C "$TMP/fastapi" sparse-checkout set docs/fr/docs >/dev/null 2>&1

mkdir -p "$DEST"
rm -f "$DEST"/*.md

# Aplatissement : chemin -> nom de fichier, pour garder un identifiant de document lisible.
find "$TMP/fastapi/docs/fr/docs" -name '*.md' | while read -r f; do
    rel="${f#"$TMP/fastapi/docs/fr/docs/"}"
    cp "$f" "$DEST/${rel//\//__}"
done

# Fichier de test interne au dépôt FastAPI, sans valeur documentaire.
rm -f "$DEST/_llm-test.md"

COMMIT=$(git -C "$TMP/fastapi" rev-parse --short HEAD)
echo "$COMMIT" > "$DEST/.corpus_commit"
echo "$(find "$DEST" -name '*.md' | wc -l) documents, $(du -sh "$DEST" | cut -f1) — fastapi@$COMMIT"
