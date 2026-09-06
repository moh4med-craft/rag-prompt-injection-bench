"""Le pipeline RAG complet : question -> passages -> prompt -> réponse.

Contrainte de conception : cette fonction est le SEUL endroit où l'on assemble un
prompt destiné au modèle de réponse. Le banc mesure l'effet de modifications de
cet assemblage ; s'il existait deux chemins d'assemblage, les mesures seraient
incomparables.

Chaque appel renvoie non seulement la réponse, mais l'intégralité de ce qui a
servi à la produire : passages récupérés, prompt final, décisions des défenses.
Sans cette traçabilité, « l'attaque n°14 a réussi » est une affirmation
invérifiable — et un banc d'essai invérifiable ne vaut rien.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .config import Config
from .defenses.detect import classify_with_llm, heuristic_flags
from .defenses.output_guard import guard_output
from .llm import Completion, LLMClient
from .prompts import (
    format_context_naive,
    format_context_spotlighted,
    system_prompt,
    user_prompt,
)
from .store import Retrieved, Store


@dataclass
class Answer:
    question: str
    text: str
    retrieved: list[Retrieved]
    system: str
    user: str
    used: list[Retrieved]
    latency_ms: int
    config_label: str
    run_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Journal des interventions des défenses : quelle couche a agi, sur quoi.
    defense_events: list[dict] = field(default_factory=list)

    @property
    def cited_docs(self) -> list[str]:
        return sorted({r.doc_id for r in self.retrieved})

    def to_record(self) -> dict:
        d = asdict(self)
        d["retrieved"] = [asdict(r) for r in self.retrieved]
        d["used"] = [asdict(r) for r in self.used]
        return d


class Pipeline:
    def __init__(self, config: Config, store: Store | None = None,
                 llm: LLMClient | None = None) -> None:
        self.config = config
        self.store = store or Store(config)
        self.llm = llm or LLMClient(config)

    def answer(self, question: str) -> Answer:
        cfg = self.config
        events: list[dict] = []

        passages: list[Retrieved] = self.store.search(question, cfg.top_k)

        # D3 puis D4 : filtrage des passages AVANT construction du prompt. L'ordre
        # est délibéré — l'heuristique est gratuite, le classifieur coûte un appel
        # par passage, donc on ne l'invoque que sur ce que l'heuristique a laissé
        # passer. C'est aussi ce qui rend l'ablation lisible : D4 ne mesure que ce
        # que D3 rate.
        retenus: list[Retrieved] = []
        for p in passages:
            if cfg.defenses.heuristic:
                motifs = heuristic_flags(p.text)
                if motifs:
                    events.append({"defense": "heuristic", "chunk": p.chunk_id,
                                   "detail": motifs, "blocking": True})
                    continue
            if cfg.defenses.classifier and classify_with_llm(self.llm, p.text):
                events.append({"defense": "classifier", "chunk": p.chunk_id,
                               "detail": "classé INSTRUCTION", "blocking": True})
                continue
            retenus.append(p)

        texts = [p.text for p in retenus]
        if cfg.defenses.separation:
            context = format_context_spotlighted(texts, question)
        else:
            context = format_context_naive(texts)

        system = system_prompt(cfg.defenses.separation, cfg.prompt_style)
        user = user_prompt(question, context)

        completion: Completion = self.llm.chat(system, user)
        texte = completion.text

        if cfg.defenses.output_guard:
            garde = guard_output(texte, system)
            texte = garde.text
            events.extend(garde.events)

        return Answer(
            question=question,
            text=texte,
            retrieved=passages,
            used=retenus,
            system=system,
            user=user,
            latency_ms=completion.latency_ms,
            config_label=cfg.label,
            run_id=cfg.run_id,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            defense_events=events,
        )
