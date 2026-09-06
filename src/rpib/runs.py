"""Persistance des exécutions, une ligne JSON par appel.

Contrainte de conception : un banc d'essai dont on ne peut pas rejouer un cas
précis est une opinion, pas une mesure. Chaque appel écrit sa question, sa
configuration, les passages récupérés, le prompt final et la réponse brute. Quand
on demande « montre-moi un cas où l'attaque passe », on ouvre le fichier.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .config import ROOT


def run_path(nom: str) -> Path:
    d = ROOT / "results" / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{nom}.jsonl"


def write_records(nom: str, records: list[dict]) -> Path:
    path = run_path(nom)
    horodatage = datetime.now(UTC).isoformat(timespec="seconds")
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps({"ts": horodatage, **r}, ensure_ascii=False) + "\n")
    return path


def read_records(nom: str) -> list[dict]:
    path = run_path(nom)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
