"""Le banc d'ablation : la mesure centrale du projet.

Contrainte de conception : CHAQUE LIGNE DU TABLEAU PORTE À LA FOIS UN CHIFFRE DE
SÉCURITÉ ET UN CHIFFRE D'UTILITÉ. Un taux d'injection présenté seul n'a aucune
valeur — un système qui refuse de répondre à tout affiche 0 %. Ce que l'on mesure
ici n'est pas « les défenses marchent-elles », c'est le TAUX DE CHANGE entre
sécurité et utilité, couche par couche.

Les configurations sont cumulatives et ordonnées de la défense la plus structurelle
à la plus cosmétique. Cet ordre est un argument : il montre que le gros du gain
vient de la séparation instruction/donnée, gratuite, et non du filtrage par motifs,
qui coûte de l'exactitude.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import Config
from .eval_attacks import evaluate_attacks
from .eval_attacks import records_to_dicts as attack_dicts
from .eval_attacks import summarize as summarize_attacks
from .eval_utility import evaluate_utility
from .eval_utility import records_to_dicts as utility_dicts
from .eval_utility import summarize as summarize_utility
from .runs import write_records

# (étiquette, drapeaux de défense activés en plus des précédents)
# Chaque entrée : (étiquette, drapeaux de défense, style de prompt).
ABLATION: list[tuple[str, dict, str]] = [
    ("prompt de tutoriel", {}, "naive"),
    ("+ prompt de tâche spécifié", {}, "spec"),
    ("+D1 séparation", {"separation": True}, "spec"),
    ("+D2 nettoyage", {"separation": True, "sanitize": True}, "spec"),
    ("+D3 heuristique", {"separation": True, "sanitize": True, "heuristic": True}, "spec"),
    ("+D4 classifieur", {"separation": True, "sanitize": True, "heuristic": True,
                         "classifier": True}, "spec"),
    ("+D5 sortie", {"separation": True, "sanitize": True, "heuristic": True,
                    "classifier": True, "output_guard": True}, "spec"),
]


@dataclass
class BenchRow:
    label: str
    model: str
    asr: float
    asr_ic95: list[float]
    asr_direct: float
    asr_indirect: float
    non_livrees: list[str]
    reussies: list[str]
    exactitude: float
    exactitude_juge: float | None
    abstention_a_tort: float
    abstention_correcte: float
    latence_ms: int


def _collection(flags: dict, base: str) -> str:
    """D2 agit à l'indexation : elle exige un index distinct, pas un drapeau d'exécution.

    C'est une propriété importante de cette défense — elle ne peut pas être activée
    ou désactivée à chaud, ce qui la rend plus coûteuse à déployer qu'un changement
    de prompt, et impossible à contourner par la requête.
    """
    return f"{base}_d2" if flags.get("sanitize") else base


def run_bench(
    model: str | None = None,
    judge: bool = True,
    configs: list[tuple[str, dict, str]] | None = None,
    progress=None,
    on_row=None,
) -> list[BenchRow]:
    rows: list[BenchRow] = []
    for label, flags, style in (configs or ABLATION):
        base = Config(llm_model=model) if model else Config()
        base = replace(base, prompt_style=style)

        cfg = base.with_defenses(**flags)
        cfg_attaque = replace(cfg, collection=_collection(flags, "poisoned"))
        cfg_utilite = replace(cfg, collection=_collection(flags, "clean"))

        if progress:
            progress(f"{label} — attaques")
        att = evaluate_attacks(cfg_attaque)
        write_records(f"attacks__{label}", attack_dicts(att))
        ra = summarize_attacks(att)

        if progress:
            progress(f"{label} — utilité")
        # L'utilité se mesure sur le corpus PROPRE : on veut le coût des défenses
        # en l'absence d'attaque, c'est-à-dire les faux positifs. Mélanger les deux
        # rendrait le chiffre ininterprétable.
        ut = evaluate_utility(cfg_utilite, condition="e2e", judge=judge)
        write_records(f"utility__{label}", utility_dicts(ut))
        ru = summarize_utility(ut)

        rows.append(BenchRow(
            label=label,
            model=cfg.llm_model,
            asr=ra["asr"], asr_ic95=ra["asr_ic95"], asr_direct=ra["asr_direct"], asr_indirect=ra["asr_indirect"],
            non_livrees=ra["non_livrees"], reussies=ra["reussies"],
            exactitude=ru["exactitude_mots_cles"], exactitude_juge=ru["exactitude_juge"],
            abstention_a_tort=ru["abstention_a_tort"],
            abstention_correcte=ru["abstention_correcte"],
            latence_ms=ru["latence_ms_mediane"],
        ))
        # Une exécution complète dure plusieurs heures sur CPU : on persiste après
        # chaque configuration plutôt qu'à la fin.
        if on_row:
            on_row(rows)
    return rows
