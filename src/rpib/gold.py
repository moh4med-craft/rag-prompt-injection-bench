"""Chargement du jeu de questions annoté et résolution des passages de référence.

Contrainte de conception : l'annotation désigne un PASSAGE DE TEXTE (`gold_evidence`),
jamais un identifiant de passage. Un identifiant dépend du découpage ; il deviendrait
faux dès qu'on modifie `chunk_size` — c'est-à-dire exactement au moment où l'on
balaie ce paramètre pour le choisir. La citation, elle, survit au redécoupage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT


@dataclass(frozen=True)
class GoldItem:
    id: str
    question: str
    answerable: bool
    answer_ref: str | None
    must_include: list[str]
    gold_doc: str | None
    gold_evidence: str | None


def load_gold(path: Path | None = None) -> list[GoldItem]:
    path = path or ROOT / "data" / "qa_gold.jsonl"
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(GoldItem(**json.loads(line)))
    return items


def resolve_gold_chunks(store, items: list[GoldItem]) -> dict[str, set[str]]:
    """Associe à chaque question l'ensemble des passages qui contiennent sa citation.

    Une citation peut tomber dans plusieurs passages à cause du recouvrement : tous
    sont alors des réponses correctes pour la récupération.
    """
    col = store.client.get_collection(store.collection_name)
    data = col.get(include=["documents", "metadatas"])
    resolved: dict[str, set[str]] = {}
    for item in items:
        if not item.answerable or not item.gold_evidence:
            resolved[item.id] = set()
            continue
        hits = {
            cid
            for cid, doc, meta in zip(data["ids"], data["documents"], data["metadatas"])
            if meta.get("doc_id") == item.gold_doc and item.gold_evidence in doc
        }
        resolved[item.id] = hits
    return resolved
