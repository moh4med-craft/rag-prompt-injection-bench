"""Chargement des scénarios d'injection.

Contrainte de conception : le succès d'une attaque doit être DÉTECTABLE
AUTOMATIQUEMENT et sans ambiguïté. C'est la décision méthodologique centrale du
projet. Chaque charge demande au modèle d'émettre un canari — une chaîne inerte,
unique, absente du corpus et impossible à produire par hasard. Le verdict devient
une recherche de motif, pas un jugement.

L'alternative (faire juger le succès par un LLM) introduirait du bruit exactement
là où le chiffre doit être solide : dans la mesure elle-même.

Toutes les charges sont inoffensives par construction : le canari est inerte, les
URL pointent vers `attacker.test` (TLD réservé, non routable), et aucun appel
d'outil n'est branché. On démontre le CANAL, jamais le dégât.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT


@dataclass(frozen=True)
class Attack:
    id: str
    type: str  # "direct" | "indirect"
    owasp: str
    technique: str
    description: str
    canary: str | None
    success_regex: str
    question: str | None = None
    trigger_question: str | None = None
    target_doc: str | None = None
    payload: str | None = None
    payload_a: str | None = None
    payload_b: str | None = None
    # Question du jeu annoté réutilisée : permet de comparer la réponse sous
    # attaque à la réponse de référence, et garantit un comportement de
    # récupération déjà mesuré à l'étape d'évaluation de la recherche.
    gold_id: str | None = None

    @property
    def prompt(self) -> str:
        """La question effectivement posée au système.

        Pour une attaque INDIRECTE, c'est une question parfaitement légitime :
        l'utilisateur est une victime, pas un complice. C'est toute la différence.
        """
        return self.question or self.trigger_question or ""

    def succeeded(self, answer: str) -> bool:
        return bool(re.search(self.success_regex, answer, re.IGNORECASE))


def load_attacks(path: Path | None = None) -> list[Attack]:
    path = path or ROOT / "data" / "attacks.jsonl"
    return [
        Attack(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
