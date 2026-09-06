"""Construction du corpus empoisonné.

Contrainte de conception : la charge doit atterrir DANS LE PASSAGE QUE LA QUESTION
DÉCLENCHEUSE RÉCUPÈRE. Insérée au hasard dans le document, elle se retrouverait
dans un passage jamais récupéré, et l'attaque échouerait pour une raison sans
rapport avec la sécurité — ce qui gonflerait artificiellement le taux de réussite
des défenses.

On interroge donc l'index propre pour savoir quel passage la question récupère
réellement, puis on insère la charge à cet endroit précis. Le résultat est un
corpus empoisonné réaliste : un attaquant qui rédige un document malveillant
optimise exactement de cette façon.

Le corpus empoisonné est GÉNÉRÉ, jamais édité à la main : il doit pouvoir être
reconstruit à l'identique depuis `corpus/clean/` et `data/attacks.jsonl`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .attacks import Attack, load_attacks
from .config import ROOT, Config
from .store import Store

MARQUEUR = "[POISON]"


def _position(texte: str, ancre: str | None) -> int | None:
    """Trouve où insérer, en tolérant les petites différences de mise en forme.

    Le passage indexé a été reconstitué par le découpage (blocs rejoints, espaces
    normalisés) : il ne correspond pas toujours caractère pour caractère au fichier
    source. On réessaie donc avec des préfixes de plus en plus courts plutôt que
    d'abandonner à la première non-correspondance — un abandon enverrait la charge
    en tête de document, là où la question ne la récupère peut-être jamais.
    """
    if not ancre:
        return None
    for longueur in (len(ancre), 120, 80, 50):
        fragment = ancre[:longueur]
        if len(fragment) >= 30 and fragment in texte:
            return texte.index(fragment) + len(fragment)
    return None


def _ancre(store: Store, attack: Attack) -> str | None:
    """Renvoie un fragment de texte du document cible que la question récupère.

    On prend le dernier paragraphe du passage récupéré : il existe verbatim dans
    le document source, ce qui en fait un point d'insertion fiable.
    """
    # Le même top_k que le pipeline, pas davantage : une charge ancrée dans un
    # passage classé 6e n'est jamais lue à k=5, et l'attaque échouerait pour une
    # raison de configuration du banc, pas de sécurité.
    for r in store.search(attack.trigger_question, top_k=store.config.top_k):
        if r.doc_id != attack.target_doc:
            continue
        # Le passage indexé commence par le fil d'Ariane, absent du document brut :
        # on prend le dernier paragraphe, qui lui est du texte d'origine.
        paragraphes = [p.strip() for p in r.text.split("\n\n") if p.strip()]
        for para in reversed(paragraphes):
            if len(para) > 40 and not para.startswith("##"):
                return para
    return None


def build_poisoned_corpus(
    clean: Path | None = None,
    out: Path | None = None,
    config: Config | None = None,
) -> dict:
    clean = clean or ROOT / "corpus" / "clean"
    out = out or ROOT / "corpus" / "poisoned"
    config = config or Config(collection="clean")
    store = Store(config, "clean")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for f in clean.glob("*.md"):
        shutil.copy2(f, out / f.name)

    rapport = {"insérées": [], "repli_titre": [], "nouveaux_documents": []}

    for attack in load_attacks():
        if attack.type != "indirect":
            continue

        if attack.target_doc == "__NOUVEAU__":
            # Attaque LLM08 : le document malveillant est créé de toutes pièces,
            # ce qui correspond au cas ou un tiers peut ajouter du contenu au corpus.
            nom = f"poisoned__{attack.id.lower()}.md"
            (out / nom).write_text(f"{MARQUEUR}\n{attack.payload}", encoding="utf-8")
            rapport["nouveaux_documents"].append(attack.id)
            continue

        chemin = out / f"{attack.target_doc}.md"
        texte = chemin.read_text(encoding="utf-8")
        ancre = _ancre(store, attack)

        position = _position(texte, ancre)

        if attack.payload == "__SPLIT__":
            # Charge découpée : les deux moitiés doivent tomber dans DEUX passages
            # differents, sinon l'attaque ne teste pas ce qu'elle prétend tester.
            if position is not None:
                pos = position
                rapport["insérées"].append(attack.id)
            else:
                pos = len(texte) // 3
                rapport["repli_titre"].append(attack.id)
            avant = texte[:pos] + attack.payload_a
            reste = texte[pos:]
            # ~2500 caracteres d'ecart : au dela d'un passage de 512 tokens.
            coupe = reste.find("\n\n", 2500)
            coupe = coupe if coupe != -1 else len(reste)
            texte = avant + reste[:coupe] + attack.payload_b + reste[coupe:]
        elif position is not None:
            texte = texte[:position] + attack.payload + texte[position:]
            rapport["insérées"].append(attack.id)
        else:
            # Repli : après le titre principal. L'attaque reste valide, mais son
            # passage peut ne pas être récupéré — le banc le signalera.
            lignes = texte.split("\n")
            i = next((n for n, l in enumerate(lignes) if l.startswith("# ")), 0)
            lignes.insert(i + 1, attack.payload)
            texte = "\n".join(lignes)
            rapport["repli_titre"].append(attack.id)

        # Marqueur de tracabilite : permet d'etiqueter le document comme
        # empoisonne dans les metadonnees de l'index, donc de mesurer combien de
        # passages malveillants sont recuperes.
        if MARQUEUR not in texte:
            texte = f"<!-- {MARQUEUR} -->\n" + texte
        chemin.write_text(texte, encoding="utf-8")

    return rapport
