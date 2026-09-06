"""Évaluation de l'ÉTAGE 2 du RAG : la génération.

Trois conditions, et c'est leur ÉCART qui informe :

- `oracle`   : on fournit au modèle les passages de référence. Mesure ce dont le
               modèle est capable quand la récupération est parfaite. C'est le
               plafond atteignable.
- `e2e`      : le pipeline complet. C'est le chiffre réel du système.
- `closed`   : aucun contexte. Contrôle indispensable — il mesure ce que le modèle
               savait DÉJÀ. Sans lui, on attribue au RAG des réponses que le modèle
               produisait sans lui, et l'on surestime son apport.

Si `oracle` est bon et `e2e` mauvais, le problème est dans la récupération.
Si `oracle` est mauvais aussi, le problème est le modèle ou le prompt.
Cette décomposition est exactement ce qu'on demande en entretien.

Notation : deux juges indépendants, dont on mesure le désaccord.
- mots-clés annotés à la main : robuste, vérifiable, aveugle aux paraphrases ;
- juge LLM : tolère les paraphrases, mais bruité.
Publier les deux et leur taux d'accord est plus honnête que d'en choisir un.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

from .config import Config
from .gold import GoldItem, load_gold, resolve_gold_chunks
from .llm import LLMClient
from .prompts import (
    JUDGE_SYSTEM,
    format_context_naive,
    format_context_spotlighted,
    system_prompt,
    user_prompt,
)
from .store import Store

ABSTENTION = re.compile(r"je ne (trouve|sais) pas|pas la réponse|aucune information", re.IGNORECASE)
CITATION = re.compile(r"\[(\d+)\]")


def _plat(texte: str) -> str:
    """Comparaison insensible à la casse ET aux accents.

    « Threadpool » et « threadpool », « réponse » et « reponse » : un critère de
    correction qui échoue sur un accent mesure l'orthographe, pas la connaissance.
    """
    sans_accents = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in sans_accents if unicodedata.category(c) != "Mn")


@dataclass
class UtilityRecord:
    id: str
    question: str
    condition: str
    answerable: bool
    answer: str
    correct_keywords: bool
    correct_judge: bool | None
    abstained: bool
    cites_source: bool
    latency_ms: int
    n_retrieved: int


def _keywords_ok(item: GoldItem, reponse: str) -> bool:
    if not item.answerable:
        # Pour une question sans réponse, la seule réponse correcte est l'abstention.
        return bool(ABSTENTION.search(reponse))
    plate = _plat(reponse)
    return all(_plat(m) in plate for m in item.must_include)


def _judge(llm: LLMClient, item: GoldItem, reponse: str) -> bool | None:
    if not item.answerable:
        return None
    verdict = llm.chat(
        JUDGE_SYSTEM,
        f"Question : {item.question}\n\nRéponse de référence : {item.answer_ref}\n\n"
        f"Réponse candidate : {reponse}",
    ).text
    return verdict.strip().upper().startswith("CORRECT")


def evaluate_utility(
    config: Config,
    condition: str = "e2e",
    store: Store | None = None,
    llm: LLMClient | None = None,
    items: list[GoldItem] | None = None,
    judge: bool = True,
) -> list[UtilityRecord]:
    store = store or Store(config)
    llm = llm or LLMClient(config)
    items = items or load_gold()
    gold = resolve_gold_chunks(store, items)
    col = store.client.get_collection(store.collection_name)

    records: list[UtilityRecord] = []
    for item in items:
        if condition == "closed":
            passages = []
        elif condition == "oracle":
            ids = sorted(gold[item.id])
            passages = col.get(ids=ids, include=["documents"])["documents"] if ids else []
        else:
            passages = [r.text for r in store.search(item.question, config.top_k)]

        if config.defenses.separation:
            contexte = format_context_spotlighted(passages, item.question)
        else:
            contexte = format_context_naive(passages)

        completion = llm.chat(
            system_prompt(config.defenses.separation, config.prompt_style),
            user_prompt(item.question, contexte),
        )
        reponse = completion.text
        records.append(UtilityRecord(
            id=item.id,
            question=item.question,
            condition=condition,
            answerable=item.answerable,
            answer=reponse,
            correct_keywords=_keywords_ok(item, reponse),
            correct_judge=_judge(llm, item, reponse) if judge else None,
            abstained=bool(ABSTENTION.search(reponse)),
            cites_source=bool(CITATION.search(reponse)),
            latency_ms=completion.latency_ms,
            n_retrieved=len(passages),
        ))
    return records


def summarize(records: list[UtilityRecord]) -> dict:
    repondables = [r for r in records if r.answerable]
    sans_reponse = [r for r in records if not r.answerable]
    accord = [r for r in repondables if r.correct_judge is not None]
    return {
        "n": len(records),
        "exactitude_mots_cles": sum(r.correct_keywords for r in repondables) / max(len(repondables), 1),
        "exactitude_juge": (sum(bool(r.correct_judge) for r in accord) / len(accord)) if accord else None,
        "accord_juges": (sum(r.correct_keywords == bool(r.correct_judge) for r in accord)
                         / len(accord)) if accord else None,
        "abstention_correcte": sum(r.abstained for r in sans_reponse) / max(len(sans_reponse), 1),
        # Abstention sur une question qui AVAIT une réponse : le coût caché des
        # défenses trop agressives.
        "abstention_a_tort": sum(r.abstained for r in repondables) / max(len(repondables), 1),
        "citation": sum(r.cites_source for r in repondables) / max(len(repondables), 1),
        "latence_ms_mediane": sorted(r.latency_ms for r in records)[len(records) // 2] if records else 0,
    }


def records_to_dicts(records: list[UtilityRecord]) -> list[dict]:
    return [asdict(r) for r in records]
