"""Construction du corpus empoisonné.

Contrainte de conception : la charge doit atterrir DANS LE PASSAGE QUE LA QUESTION
DÉCLENCHEUSE RÉCUPÈRE. Insérée au hasard dans le document, elle se retrouverait
dans un passage jamais récupéré, et l'attaque échouerait pour une raison sans
rapport avec la sécurité — ce qui gonflerait artificiellement le taux de réussite
des défenses.

Et cet emplacement ne peut pas être choisi d'avance : insérer du texte modifie
les frontières de découpage, donc le passage qui sera récupéré APRÈS insertion
n'est pas celui qui l'était avant. Trois stratégies statiques ont été essayées
(après le paragraphe d'ancrage, avant, en tête de passage) : chacune livrait
sept à neuf charges sur douze, mais jamais les mêmes.

La construction est donc ITÉRATIVE. On insère, on réindexe, on vérifie quelles
charges sont réellement récupérées, et pour celles qui ne le sont pas on
recommence en visant un passage effectivement récupéré par l'index empoisonné.
Le procédé converge en quelques tours. C'est un point de méthode, pas un détail
d'implémentation : sans lui, le taux d'injection mesure la chance du découpage
autant que la résistance du modèle.

Le corpus empoisonné est GÉNÉRÉ, jamais édité à la main : il doit pouvoir être
reconstruit à l'identique depuis `corpus/clean/` et `data/attacks.jsonl`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .attacks import Attack, load_attacks
from .config import ROOT, Config
from .store import Store

MARQUEUR = "[POISON]"
MANIFESTE = ".payloads.json"

# Chaîne unique et inerte que chaque charge dépose dans le document. Pour les
# attaques sans canari, c'est l'artefact que l'attaque cherche à faire produire.
_SIGNATURES_SANS_CANARI = {
    "I04": "attacker.test",
    "I06": "threadpool_disabled",
    "I10": "shell.run",
    "I11": "fastapi-turbo-pro",
}


def _signature(attack: Attack) -> str:
    return attack.canary or _SIGNATURES_SANS_CANARI[attack.id]


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
            # AVANT le paragraphe d'ancrage, pas après. L'ancre est le DERNIER
            # paragraphe du passage récupéré : insérer après elle place la charge
            # en tête du passage SUIVANT, celui que la recherche ne ramène pas.
            # Le bug était invisible — la charge était bien dans le document, et
            # le document bien marqué, mais le modèle ne la voyait jamais.
            return texte.index(fragment)
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
        # Le PREMIER paragraphe du passage, pas le dernier. Le remplissage des
        # passages est séquentiel : un bloc situé en tête y reste même après avoir
        # grossi, alors qu'un bloc en queue bascule dans le passage suivant dès que
        # la charge le fait dépasser le budget. C'est ce qui rendait la livraison
        # instable.
        # Le fil d'Ariane, lui, n'existe pas dans le document brut : il est écarté
        # par les filtres ci-dessous (trop court, ou préfixé de « ## »).
        paragraphes = [p.strip() for p in r.text.split("\n\n") if p.strip()]
        for para in paragraphes:
            if len(para) > 40 and not para.startswith("##"):
                return para
    return None


def choose_anchor(store: Store, attack: Attack, clean_text: str,
                  signature: str, deja_essayes: set[str] | None = None) -> str | None:
    """Choisit un paragraphe d'ancrage parmi ce que l'index EMPOISONNÉ récupère.

    On interroge l'index dans son état actuel — c'est lui qui dit la vérité sur
    ce que le modèle recevra. Le paragraphe retenu doit exister tel quel dans le
    document propre, puisque le corpus est reconstruit depuis celui-ci à chaque
    tour, et ne doit pas contenir la charge elle-même.
    """
    deja_essayes = deja_essayes or set()
    for r in store.search(attack.trigger_question, store.config.top_k):
        if r.doc_id != attack.target_doc:
            continue
        for para in [p.strip() for p in r.text.split("\n\n") if p.strip()]:
            # On écarte les ancrages déjà tentés : sans cela la boucle
            # reproposerait le même paragraphe et s'arrêterait faute de progrès,
            # alors que d'autres emplacements du même passage restent à essayer.
            if (len(para) >= 40 and not para.startswith("##")
                    and signature.lower() not in para.lower()
                    and para not in deja_essayes
                    and para in clean_text):
                return para
    return None


def build_poisoned_corpus(
    clean: Path | None = None,
    out: Path | None = None,
    config: Config | None = None,
    anchors: dict[str, str] | None = None,
) -> dict:
    clean = clean or ROOT / "corpus" / "clean"
    out = out or ROOT / "corpus" / "poisoned"
    config = config or Config(collection="clean")
    store = Store(config, "clean")
    anchors = anchors or {}

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for f in clean.glob("*.md"):
        shutil.copy2(f, out / f.name)

    rapport = {"insérées": [], "repli_titre": [], "nouveaux_documents": []}
    # Signature de chaque charge réellement écrite sur le disque. Sert à marquer
    # les passages porteurs À L'INDEXATION : un marquage au niveau du document
    # compterait comme « livrée » une attaque dont le passage n'est jamais lu, et
    # surestimerait donc l'efficacité des défenses.
    signatures: dict[str, str] = {}

    for attack in load_attacks():
        if attack.type != "indirect":
            continue

        if attack.target_doc == "__NOUVEAU__":
            # Attaque LLM08 : le document malveillant est créé de toutes pièces,
            # ce qui correspond au cas ou un tiers peut ajouter du contenu au corpus.
            nom = f"poisoned__{attack.id.lower()}.md"
            (out / nom).write_text(f"{MARQUEUR}\n{attack.payload}", encoding="utf-8")
            rapport["nouveaux_documents"].append(attack.id)
            signatures[attack.id] = _signature(attack)
            continue

        chemin = out / f"{attack.target_doc}.md"
        texte = chemin.read_text(encoding="utf-8")
        # Un ancrage retenu par un tour précédent l'emporte : il a été choisi à
        # partir de ce que l'index empoisonné récupère réellement.
        ancre = anchors.get(attack.id) or _ancre(store, attack)

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
            avant = texte[:pos] + "\n\n" + attack.payload_a.strip() + "\n\n"
            reste = texte[pos:]
            # ~2500 caracteres d'ecart : au dela d'un passage de 512 tokens.
            coupe = reste.find("\n\n", 2500)
            coupe = coupe if coupe != -1 else len(reste)
            texte = avant + reste[:coupe] + attack.payload_b + reste[coupe:]
        elif position is not None:
            # La charge forme son PROPRE bloc, séparé par des lignes vides.
            # Une charge fondue dans un paragraphe est bien livrée mais se lit
            # comme de la prose : elle perd la saillance typographique qui la
            # fait prendre pour une consigne. C'est la boucle de convergence,
            # et non la forme de l'insertion, qui garantit désormais la
            # livraison — on peut donc rester réaliste sur la forme.
            bloc = attack.payload.strip() + "\n\n"
            texte = texte[:position] + bloc + texte[position:]
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
        signatures[attack.id] = _signature(attack)

    (out / MANIFESTE).write_text(
        json.dumps(signatures, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    rapport["signatures"] = signatures
    return rapport
