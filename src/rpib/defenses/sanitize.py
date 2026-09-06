"""D2 — Nettoyage à l'ingestion.

Contrainte de conception : nettoyer À L'ENTRÉE du système, jamais à la sortie.
Un caractère de largeur nulle ou un commentaire HTML qui atteint la base
vectorielle est un problème permanent : il sera récupéré à chaque requête
pertinente, pour chaque utilisateur.

Cette couche ne cherche PAS à détecter des instructions malveillantes (c'est D3).
Elle supprime les canaux qui permettent de cacher du texte à un relecteur humain
tout en le laissant visible pour le modèle. C'est une différence importante :
D2 traite un problème de représentation, pas un problème de contenu.
"""

from __future__ import annotations

import re
import unicodedata

# Largeur nulle, jointures, marques directionnelles : invisibles à l'écran,
# parfaitement lisibles par un tokenizer.
_INVISIBLES = re.compile(
    "["
    "\u200b-\u200f"  # largeur nulle, jointures, marques directionnelles
    "\u202a-\u202e"  # incorporations et forçages bidirectionnels
    "\u2060-\u2064"  # jointures et séparateurs invisibles
    "\u206a-\u206f"  # anciennes commandes de formatage
    "\ufeff"          # marque d'ordre des octets
    "\u00ad"          # trait d'union conditionnel
    "]"
)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Texte rendu invisible par le style plutôt que par le caractère.
_HIDDEN_STYLE = re.compile(
    r"<([a-z]+)[^>]*style=[\"'][^\"']*(display\s*:\s*none|font-size\s*:\s*0|"
    r"color\s*:\s*#?f{3,6}\b)[^\"']*[\"'][^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)


def clean_document(text: str) -> str:
    """Rend visible ce qui est lisible par le modèle, et supprime le reste."""
    # NFKC : réduit les homoglyphes et les variantes de compatibilité à leur forme
    # canonique, ce qui neutralise l'obfuscation par caractères Unicode exotiques.
    text = unicodedata.normalize("NFKC", text)
    text = _HTML_COMMENT.sub(" ", text)
    text = _HIDDEN_STYLE.sub(" ", text)
    text = _INVISIBLES.sub("", text)
    return text
