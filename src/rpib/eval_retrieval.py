"""Évaluation de l'ÉTAGE 1 du RAG : la récupération, isolée de la génération.

Pourquoi cette mesure d'abord : si le bon passage n'est pas récupéré, aucun modèle
de langage ne peut produire la bonne réponse. Un mauvais Recall plafonne
définitivement l'exactitude finale. Et cette mesure ne coûte aucun appel au LLM :
elle tourne en quelques secondes, ce qui la rend utilisable pour BALAYER les
paramètres de découpage — au lieu de les choisir à l'intuition.

Deux métriques, complémentaires :
- Recall@k : le bon passage est-il quelque part dans les k récupérés ? C'est le
  plafond de performance de tout ce qui suit.
- MRR : à quel RANG ? Un passage correct en 5e position pèse moins qu'en 1re,
  parce que les modèles accordent plus d'attention au début de leur contexte.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .gold import GoldItem, load_gold, resolve_gold_chunks
from .store import Store


@dataclass
class RetrievalReport:
    """Deux niveaux de granularité, et l'écart entre les deux est informatif.

    Le niveau PASSAGE exige que le passage contenant la citation soit récupéré.
    C'est la mesure stricte, celle qui compte pour la qualité du contexte fourni.

    Le niveau DOCUMENT n'exige que le bon document. Moins exigeant, mais plus
    proche de la question réelle « le modèle a-t-il de quoi répondre ? » : un autre
    passage du même document contient souvent l'information sous une autre forme.

    Ne publier que la stricte sous-estime le système ; ne publier que la souple le
    flatte. On publie les deux, et l'écart mesure la sensibilité au découpage.
    """

    recall: dict[int, float]
    recall_doc: dict[int, float]
    mrr: float
    mrr_doc: float
    non_resolus: list[str]
    details: list[dict]

    def as_rows(self) -> list[tuple[str, str, str]]:
        rows = [
            (f"Recall@{k}", f"{self.recall[k]:.1%}", f"{self.recall_doc[k]:.1%}")
            for k in sorted(self.recall)
        ]
        rows.append(("MRR", f"{self.mrr:.3f}", f"{self.mrr_doc:.3f}"))
        return rows


def evaluate_retrieval(
    config: Config,
    store: Store | None = None,
    items: list[GoldItem] | None = None,
    ks: tuple[int, ...] = (1, 3, 5, 10),
) -> RetrievalReport:
    store = store or Store(config)
    items = items or load_gold()
    answerable = [i for i in items if i.answerable]
    gold = resolve_gold_chunks(store, answerable)

    # Une citation introuvable dans TOUT l'index signale soit une annotation
    # fausse, soit un découpage qui l'a coupée en deux. Dans les deux cas la
    # question doit être exclue du calcul, pas comptée comme un échec.
    non_resolus = [i.id for i in answerable if not gold[i.id]]
    utilisables = [i for i in answerable if gold[i.id]]

    kmax = max(ks)
    hits = {k: 0 for k in ks}
    hits_doc = {k: 0 for k in ks}
    rangs: list[float] = []
    rangs_doc: list[float] = []
    details = []

    for item in utilisables:
        resultats = store.search(item.question, top_k=kmax)
        rang_chunk = next(
            (n for n, r in enumerate(resultats, 1) if r.chunk_id in gold[item.id]), None
        )
        rang_doc = next(
            (n for n, r in enumerate(resultats, 1) if r.doc_id == item.gold_doc), None
        )
        for k in ks:
            hits[k] += rang_chunk is not None and rang_chunk <= k
            hits_doc[k] += rang_doc is not None and rang_doc <= k
        rangs.append(1.0 / rang_chunk if rang_chunk else 0.0)
        rangs_doc.append(1.0 / rang_doc if rang_doc else 0.0)
        details.append({
            "id": item.id,
            "question": item.question,
            "rang": rang_chunk,
            "rang_doc": rang_doc,
            "top1_doc": resultats[0].doc_id if resultats else None,
            "top1_score": resultats[0].score if resultats else None,
            "gold_doc": item.gold_doc,
        })

    n = len(utilisables) or 1
    return RetrievalReport(
        recall={k: hits[k] / n for k in ks},
        recall_doc={k: hits_doc[k] / n for k in ks},
        mrr=sum(rangs) / n,
        mrr_doc=sum(rangs_doc) / n,
        non_resolus=non_resolus,
        details=details,
    )
