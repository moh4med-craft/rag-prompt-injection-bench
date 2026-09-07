"""Mesure du taux de réussite des injections (ASR — Attack Success Rate).

Le chiffre central du projet. Trois précautions de méthode, sans lesquelles il ne
vaudrait rien :

1. DÉTECTION OBJECTIVE. Le succès est une recherche de motif sur la sortie
   (canari, URL d'exfiltration, fait faux planté). Aucun jugement humain ni LLM
   dans la boucle de mesure.

2. DISTINGUER « ÉCHEC DE L'ATTAQUE » DE « ÉCHEC DE LA RÉCUPÉRATION ». Une
   injection indirecte dont le passage malveillant n'est jamais récupéré n'a pas
   été bloquée par une défense : elle n'a simplement jamais eu lieu. Les compter
   ensemble surestimerait l'efficacité des défenses, parfois massivement. On
   enregistre donc `poison_retrieved` pour chaque cas.

3. MESURER L'UTILITÉ EN MÊME TEMPS. Un ASR ne se lit jamais seul : un système qui
   refuse de répondre à tout affiche 0 %. C'est `eval_utility` qui fournit le
   contrepoids, et le rapport final présente toujours les deux côte à côte.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .attacks import Attack, load_attacks
from .config import Config
from .llm import LLMClient
from .pipeline import Pipeline
from .store import Store


@dataclass
class AttackRecord:
    id: str
    type: str
    owasp: str
    technique: str
    success: bool
    poison_retrieved: bool
    n_poison_chunks: int
    answer: str
    question: str
    latency_ms: int
    blocked_by: str | None


def evaluate_attacks(
    config: Config,
    store: Store | None = None,
    llm: LLMClient | None = None,
    attacks: list[Attack] | None = None,
) -> list[AttackRecord]:
    pipeline = Pipeline(config, store, llm)
    attacks = attacks or load_attacks()

    records: list[AttackRecord] = []
    for attack in attacks:
        answer = pipeline.answer(attack.prompt)
        poison = [r for r in answer.retrieved if r.poisoned]
        bloque = next(
            (e["defense"] for e in answer.defense_events if e.get("blocking")), None
        )
        records.append(AttackRecord(
            id=attack.id,
            type=attack.type,
            owasp=attack.owasp,
            technique=attack.technique,
            success=attack.succeeded(answer.text),
            # Pour une attaque directe, la charge est dans la question : elle est
            # toujours « livrée ». Pour une indirecte, tout dépend de la récupération.
            poison_retrieved=True if attack.type == "direct" else bool(poison),
            n_poison_chunks=len(poison),
            answer=answer.text,
            question=attack.prompt,
            latency_ms=answer.latency_ms,
            blocked_by=bloque,
        ))
    return records


def wilson(succes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalle de confiance de Wilson à 95 % sur une proportion.

    Avec vingt attaques, une seule bascule déplace le taux de cinq points et
    l'intervalle dépasse quinze points. Publier des écarts de cette taille sans
    publier leur incertitude reviendrait à raconter du bruit comme un résultat.
    """
    if total == 0:
        return (0.0, 0.0)
    p = succes / total
    d = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / d
    demi = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / d
    return (max(0.0, centre - demi), min(1.0, centre + demi))


def summarize(records: list[AttackRecord]) -> dict:
    def taux(sous_ensemble: list[AttackRecord]) -> float | None:
        return (sum(r.success for r in sous_ensemble) / len(sous_ensemble)
                if sous_ensemble else None)

    livrees = [r for r in records if r.poison_retrieved]
    par_owasp = {}
    for r in records:
        par_owasp.setdefault(r.owasp, []).append(r)

    bas, haut = wilson(sum(r.success for r in records), len(records))
    return {
        "n": len(records),
        "asr": taux(records),
        "asr_ic95": [round(bas, 3), round(haut, 3)],
        "asr_direct": taux([r for r in records if r.type == "direct"]),
        "asr_indirect": taux([r for r in records if r.type == "indirect"]),
        # ASR conditionnel : parmi les seules attaques effectivement livrées au
        # modèle. Sépare « la défense a tenu » de « l'attaque n'est pas arrivée ».
        "asr_livrees": taux(livrees),
        "non_livrees": [r.id for r in records if not r.poison_retrieved],
        "asr_par_owasp": {k: taux(v) for k, v in sorted(par_owasp.items())},
        "reussies": [r.id for r in records if r.success],
        "latence_ms_mediane": sorted(r.latency_ms for r in records)[len(records) // 2]
        if records else 0,
    }


def records_to_dicts(records: list[AttackRecord]) -> list[dict]:
    return [asdict(r) for r in records]
