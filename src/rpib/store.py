"""Indexation et récupération : le modèle d'embedding et la base vectorielle.

Deux contraintes de conception :

1. LES PRÉFIXES E5 SONT OBLIGATOIRES. `multilingual-e5-small` a été entraîné avec
   « query: » devant les requêtes et « passage: » devant les documents. Les omettre
   ne produit aucune erreur — juste un Recall silencieusement dégradé de plus de dix
   points. C'est le piège le plus coûteux du RAG en français.

2. LE COMPTAGE DE TOKENS UTILISE LE TOKENIZER DU MODÈLE, pas une approximation.
   Au-delà de 512 tokens, le modèle tronque sans prévenir : la fin du passage n'est
   pas indexée, et personne ne s'en aperçoit.

Chroma plutôt que FAISS : on doit pouvoir filtrer et étiqueter par métadonnées
(`poisoned`, `doc_id`), ce que FAISS ne stocke pas. À l'échelle du projet
(quelques milliers de passages) la différence de vitesse est nulle.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings

from .chunking import Chunk, split_markdown
from .config import Config


@dataclass(frozen=True)
class Retrieved:
    chunk_id: str
    doc_id: str
    text: str
    heading: str
    score: float
    poisoned: bool


@functools.lru_cache(maxsize=2)
def _load_embedder(model_name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


class Store:
    def __init__(self, config: Config, collection: str | None = None) -> None:
        self.config = config
        self.collection_name = collection or config.collection
        self.model = _load_embedder(config.embedding_model)
        self.client = chromadb.PersistentClient(
            path=config.chroma_path,
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )

    # ----------------------------------------------------------------- #
    # Embedding
    # ----------------------------------------------------------------- #
    def count_tokens(self, text: str) -> int:
        # `verbose=False` : le tokenizer avertit dès qu'un texte dépasse 512 tokens,
        # y compris quand on ne fait que le MESURER. L'avertissement laisse croire
        # à une troncature alors qu'aucun encodage n'a lieu ici.
        return len(self.model.tokenizer.encode(text, add_special_tokens=False,
                                               verbose=False))

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        prefixed = [f"passage: {t}" for t in texts]
        return self.model.encode(
            prefixed, normalize_embeddings=True, batch_size=32, show_progress_bar=False
        ).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.model.encode(
            [f"query: {text}"], normalize_embeddings=True, show_progress_bar=False
        )[0].tolist()

    # ----------------------------------------------------------------- #
    # Indexation
    # ----------------------------------------------------------------- #
    def build(self, corpus_dir: Path, sanitize: bool = False) -> dict:
        """(Ré)indexe entièrement un répertoire de documents markdown.

        Reconstruction complète et non incrémentale : une indexation partielle
        rendrait les mesures dépendantes de l'historique de la machine.
        """
        from .defenses.sanitize import clean_document

        if self.collection_name in [c.name for c in self.client.list_collections()]:
            self.client.delete_collection(self.collection_name)
        col = self.client.create_collection(
            self.collection_name,
            # Cosinus : E5 produit des vecteurs normalisés, c'est la métrique attendue.
            metadata={"hnsw:space": "cosine"},
        )

        # Signatures des charges, écrites par poison.py. En leur absence (corpus
        # propre), aucun passage n'est marqué.
        manifeste = corpus_dir / ".payloads.json"
        signatures: list[str] = []
        if manifeste.exists():
            import json

            signatures = [s.lower() for s in json.loads(
                manifeste.read_text(encoding="utf-8")).values()]

        chunks: list[Chunk] = []
        for path in sorted(corpus_dir.glob("*.md")):
            raw = path.read_text(encoding="utf-8", errors="replace")
            if sanitize:
                raw = clean_document(raw)
            doc_id = path.stem
            chunks.extend(
                split_markdown(
                    raw,
                    doc_id,
                    chunk_size=self.config.chunk_size,
                    overlap=self.config.chunk_overlap,
                    count_tokens=self.count_tokens,
                )
            )

        texts = [c.text for c in chunks]
        embeddings = self.embed_passages(texts)
        col.add(
            ids=[c.chunk_id for c in chunks],
            documents=texts,
            embeddings=embeddings,
            metadatas=[
                {
                    "doc_id": c.doc_id,
                    "heading": c.heading,
                    "position": c.position,
                    # Au niveau du PASSAGE : seul celui qui porte réellement la
                    # charge compte comme livré au modèle.
                    "poisoned": any(sig in c.text.lower() for sig in signatures),
                }
                for c in chunks
            ],
        )
        lengths = [self.count_tokens(t) for t in texts]
        return {
            "documents": len({c.doc_id for c in chunks}),
            "chunks": len(chunks),
            "tokens_moyen": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            "tokens_max": max(lengths, default=0),
            "depassements_512": sum(1 for n in lengths if n > 512),
        }

    # ----------------------------------------------------------------- #
    # Récupération
    # ----------------------------------------------------------------- #
    def search(self, question: str, top_k: int | None = None) -> list[Retrieved]:
        col = self.client.get_collection(self.collection_name)
        res = col.query(
            query_embeddings=[self.embed_query(question)],
            n_results=top_k or self.config.top_k,
            include=["documents", "metadatas", "distances"],
        )
        out = []
        for cid, doc, meta, dist in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            out.append(
                Retrieved(
                    chunk_id=cid,
                    doc_id=str(meta.get("doc_id", "")),
                    text=doc,
                    heading=str(meta.get("heading", "")),
                    # Chroma renvoie une distance cosinus ; on la ramène en similarité.
                    score=round(1.0 - float(dist), 4),
                    poisoned=bool(meta.get("poisoned", False)),
                )
            )
        return out
