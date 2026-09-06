"""D5 — validation de la sortie.

Cette couche part du principe que TOUTES LES COUCHES AMONT ONT ÉCHOUÉ. C'est le
seul endroit du système où l'on suppose l'injection réussie et où l'on cherche à
en limiter l'effet. C'est aussi celle que la plupart des projets oublient.

RÈGLE DE MÉTHODE, ESSENTIELLE : ce garde-fou ne cherche PAS les canaris du banc
d'essai. Il serait trivial de faire tomber l'ASR à zéro en filtrant en sortie les
chaînes exactes que les attaques demandent d'émettre — et la mesure serait
circulaire, donc mensongère. On n'implémente ici que des règles GÉNÉRIQUES, qui
auraient un sens face à des attaques qu'on n'a pas écrites :

- liste blanche d'hôtes pour les liens et images (canal d'exfiltration) ;
- détection de fuite du prompt système par recouvrement de n-grammes ;
- refus des balises d'appel d'outil dans une réponse censée être du texte.

Conséquence assumée : D5 ne bloque PAS l'émission d'un canari. C'est un résultat,
pas un défaut d'implémentation — la validation de sortie n'attrape que les
attaques dont l'effet a une forme reconnaissable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Rien n'est autorisé par défaut : la documentation citée ne nécessite aucun
# domaine externe dans une réponse textuelle.
HOTES_AUTORISES: frozenset[str] = frozenset({"fastapi.tiangolo.com"})

_LIEN = re.compile(r"!?\[[^\]]*\]\(\s*([a-z][a-z0-9+.\-]*://[^)\s]+)[^)]*\)", re.IGNORECASE)
_URL_NUE = re.compile(r"\bhttps?://([^\s/)>\"']+)", re.IGNORECASE)
_APPEL_OUTIL = re.compile(r"<tool_call>.*?</tool_call>|<function_calls>", re.DOTALL | re.IGNORECASE)

REMPLACEMENT = "[lien externe supprimé]"


@dataclass
class GuardResult:
    text: str
    events: list[dict]

    @property
    def blocked(self) -> bool:
        return any(e.get("blocking") for e in self.events)


def _hote(url: str) -> str:
    return re.sub(r"^[a-z]+://", "", url, flags=re.IGNORECASE).split("/")[0].split(":")[0].lower()


def _ngrams(texte: str, n: int = 8) -> set[str]:
    mots = re.findall(r"\w+", texte.lower())
    return {" ".join(mots[i:i + n]) for i in range(max(len(mots) - n + 1, 0))}


def guard_output(answer: str, system: str) -> GuardResult:
    events: list[dict] = []
    texte = answer

    # 1. Exfiltration par lien ou image markdown.
    for url in _LIEN.findall(texte) + [f"http://{h}" for h in _URL_NUE.findall(texte)]:
        if _hote(url) not in HOTES_AUTORISES:
            events.append({"defense": "output_guard", "rule": "hote_non_autorise",
                           "detail": _hote(url), "blocking": False})
    if events:
        texte = _LIEN.sub(REMPLACEMENT, texte)
        texte = _URL_NUE.sub(REMPLACEMENT, texte)

    # 2. Fuite du prompt système : on compare la réponse au prompt réellement
    # utilisé, pas à une chaîne codée en dur — la règle survit à une réécriture
    # du prompt.
    communs = _ngrams(texte) & _ngrams(system)
    if communs:
        events.append({"defense": "output_guard", "rule": "fuite_prompt_systeme",
                       "detail": f"{len(communs)} n-grammes communs", "blocking": True})
        texte = ("Je ne peux pas répondre : la réponse générée reproduisait mes "
                 "instructions internes.")

    # 3. Balise d'appel d'outil dans une réponse textuelle.
    if _APPEL_OUTIL.search(texte):
        events.append({"defense": "output_guard", "rule": "appel_outil",
                       "detail": "balise d'outil en sortie", "blocking": True})
        texte = _APPEL_OUTIL.sub("[appel d'outil refusé]", texte)

    return GuardResult(text=texte, events=events)
