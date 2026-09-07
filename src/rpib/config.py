"""Configuration centrale du banc d'essai.

Contrainte de conception : TOUT paramètre qui influence un résultat passe par cet
objet unique. Le projet ne mesure pas un système, il compare des configurations
entre elles (avec ou sans telle défense, tel modèle, tel découpage). Si un
paramètre était lu ailleurs, on ne pourrait plus garantir que deux exécutions ne
diffèrent que par ce qu'on croit — et le chiffre avant/après ne voudrait plus rien
dire. Chaque `Config` sait aussi produire un identifiant stable qui étiquette ses
résultats sur disque.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Defenses:
    """Les cinq couches de défense, chacune activable indépendamment.

    L'indépendance n'est pas un luxe d'architecture : c'est ce qui permet
    l'ablation. Sans elle, on constate qu'« avec les défenses c'est mieux »
    sans pouvoir attribuer le gain à une couche précise, ni mesurer ce que
    chacune coûte en exactitude.
    """

    # D1 — Séparation instruction/donnée : le contexte est balisé et marqué
    # ligne à ligne comme une donnée non fiable (spotlighting / datamarking).
    separation: bool = False
    # D2 — Nettoyage à l'ingestion : Unicode, caractères invisibles, commentaires HTML.
    sanitize: bool = False
    # D3 — Détection heuristique par motifs. Implémentée pour MESURER son insuffisance.
    heuristic: bool = False
    # D4 — Classifieur LLM sur chaque passage récupéré.
    classifier: bool = False
    # D5 — Validation de la sortie : canaris, liste blanche d'URL, ancrage.
    output_guard: bool = False

    @classmethod
    def all_on(cls) -> Defenses:
        return cls(separation=True, sanitize=True, heuristic=True, classifier=True,
                   output_guard=True)

    @property
    def label(self) -> str:
        on = [k[0].upper() + k[1:] for k, v in asdict(self).items() if v]
        return "baseline" if not on else "+".join(on)


@dataclass(frozen=True)
class Config:
    # --- Découpage ---
    chunk_size: int = int(_env("CHUNK_SIZE", "512"))
    chunk_overlap: int = int(_env("CHUNK_OVERLAP", "64"))

    # --- Récupération ---
    embedding_model: str = _env("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    top_k: int = int(_env("TOP_K", "5"))
    chroma_path: str = _env("CHROMA_PATH", str(ROOT / "chroma_db"))
    collection: str = "clean"

    # --- Génération ---
    llm_provider: str = _env("LLM_PROVIDER", "ollama")
    llm_model: str = _env("LLM_MODEL", "qwen2.5:3b-instruct")
    # Température nulle : sans elle, deux exécutions du même bench donnent deux
    # chiffres différents et l'écart avant/après devient ininterprétable.
    temperature: float = float(_env("TEMPERATURE", "0"))
    max_tokens: int = 400
    # "naive" = prompt de tutoriel ; "spec" = prompt de tâche spécifié.
    prompt_style: str = "spec"

    defenses: Defenses = field(default_factory=Defenses)

    def with_defenses(self, **flags: bool) -> Config:
        return replace(self, defenses=replace(self.defenses, **flags))

    @property
    def run_id(self) -> str:
        """Identifiant court et stable, dérivé de la configuration entière.

        Deux exécutions produisant le même `run_id` DOIVENT produire les mêmes
        résultats. Si ce n'est pas le cas, c'est qu'un paramètre échappe à cet objet.
        """
        payload = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:8]

    @property
    def label(self) -> str:
        style = "" if self.prompt_style == "spec" else f"-{self.prompt_style}"
        modele = self.llm_model.replace(":", "-").replace("/", "-")
        return f"{modele}{style}-{self.defenses.label}"

    def to_dict(self) -> dict:
        return asdict(self)
