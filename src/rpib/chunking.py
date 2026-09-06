"""Découpage des documents markdown en passages indexables.

Trois contraintes de conception, dans cet ordre d'importance :

1. NE JAMAIS COUPER AU MILIEU D'UNE STRUCTURE. Un passage qui commence au milieu
   d'une phrase, ou pire au milieu d'un bloc de code, produit un vecteur bruité.
   On découpe donc d'abord sur les frontières que l'auteur a lui-même posées :
   titres, paragraphes, blocs de code délimités.

2. RESPECTER LA LIMITE DU MODÈLE D'EMBEDDING. `multilingual-e5-small` tronque
   silencieusement au-delà de 512 tokens — pas d'erreur, pas d'avertissement, juste
   une fin de passage qui n'est pas indexée. On compte donc en TOKENS du modèle
   cible, pas en caractères. C'est le bug invisible classique du RAG.

3. CONSERVER LE CONTEXTE HIÉRARCHIQUE. Un passage isolé qui dit « il faut alors
   utiliser `Depends()` » ne s'embedde correctement que si l'on sait de quelle
   section il provient. Chaque passage est donc préfixé de son fil d'Ariane de
   titres, qui compte dans le budget de tokens.

Le comptage de tokens est injecté (`count_tokens`) plutôt qu'importé : ça garde ce
module testable sans charger un modèle de 500 Mo.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
# MkDocs suffixe ses titres d'une ancre : « ## Les dépendances { #dependencies } ».
# Indexée telle quelle, cette ancre bruite l'embedding du fil d'Ariane et gaspille
# du budget de tokens sans apporter d'information. Nettoyage de forme, pas de fond :
# il n'a rien à voir avec la sécurité et s'applique donc toujours, hors défense D2.
_ANCRE_MKDOCS = re.compile(r"\s*\{\s*#[\w.:-]+\s*\}\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    chunk_id: str
    text: str
    heading: str
    position: int


def approx_tokens(text: str) -> int:
    """Estimation grossière, uniquement pour les tests et les cas dégradés.

    Le vrai comptage utilise le tokenizer du modèle d'embedding (voir `store.py`).
    Ratio ~3,8 caractères par token, mesuré sur du français technique.
    """
    return max(1, round(len(text) / 3.8))


@dataclass
class _Block:
    """Unité atomique de découpage : un paragraphe, ou un bloc de code entier."""

    text: str
    heading_path: tuple[str, ...]


def _iter_blocks(text: str):
    """Découpe le markdown en blocs atomiques, en suivant la hiérarchie des titres.

    Les blocs de code délimités sont traités comme indivisibles : les couper
    produirait des fragments syntaxiquement absurdes, et c'est de toute façon dans
    un bloc de code qu'un lecteur cherche un exemple complet.
    """
    stack: list[str] = []
    buf: list[str] = []
    in_fence = False
    fence_marker = ""

    def flush():
        nonlocal buf
        content = "\n".join(buf).strip()
        if content:
            yield _Block(content, tuple(stack))
        buf = []

    for line in text.splitlines():
        fence = _FENCE_RE.match(line)
        if fence and not in_fence:
            yield from flush()
            in_fence, fence_marker = True, fence.group(1)
            buf.append(line)
            continue
        if in_fence:
            buf.append(line)
            if line.strip().startswith(fence_marker):
                in_fence = False
                yield from flush()
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            yield from flush()
            level = len(heading.group(1))
            title = _ANCRE_MKDOCS.sub("", heading.group(2)).strip()
            del stack[level - 1:]
            stack.append(title)
            continue

        if not line.strip():
            yield from flush()
        else:
            buf.append(line)

    yield from flush()


def _hard_split(text: str, budget: int, count_tokens: Callable[[str], int]) -> list[str]:
    """Dernier recours : un bloc unique dépasse à lui seul le budget.

    On coupe sur les fins de phrase, puis sur les fins de ligne. Un bloc de code
    trop long finit coupé ligne à ligne — c'est laid, mais c'est mieux que la
    troncature silencieuse du modèle d'embedding.
    """
    pieces = re.split(r"(?<=[.!?])\s+|\n", text)
    out: list[str] = []
    cur: list[str] = []
    for piece in pieces:
        # On vide AVANT de dépasser, pas après : vider après garantit que chaque
        # morceau produit dépasse le budget d'exactement une phrase.
        if cur and count_tokens(" ".join([*cur, piece])) > budget:
            out.append(" ".join(cur).strip())
            cur = []
        if count_tokens(piece) > budget:
            # Une « phrase » plus longue que le budget : ligne de tableau, URL
            # géante, ligne de code minifiée. Découpage mot à mot en dernier recours.
            mots: list[str] = []
            for mot in piece.split(" "):
                if mots and count_tokens(" ".join([*mots, mot])) > budget:
                    out.append(" ".join(mots))
                    mots = []
                mots.append(mot)
            if mots:
                cur = [" ".join(mots)]
            continue
        cur.append(piece)
    if cur:
        out.append(" ".join(cur).strip())
    return [p for p in out if p]


def split_markdown(
    text: str,
    doc_id: str,
    *,
    chunk_size: int = 512,
    overlap: int = 64,
    count_tokens: Callable[[str], int] = approx_tokens,
) -> list[Chunk]:
    """Découpe un document en passages d'au plus `chunk_size` tokens.

    `overlap` est le nombre de tokens de la fin du passage précédent réinjectés au
    début du suivant. Il n'existe que pour une raison : une information à cheval
    sur une frontière de découpage serait sinon perdue des deux côtés.
    """
    blocks = list(_iter_blocks(text))
    chunks: list[Chunk] = []
    cur: list[_Block] = []

    def render(bs: list[_Block]) -> tuple[str, str]:
        """Reconstitue le texte indexé d'un groupe de blocs, titres compris.

        Un passage peut couvrir plusieurs sous-sections. Si l'on se contentait du
        fil d'Ariane du PREMIER bloc, les titres des suivantes disparaîtraient de
        l'index : la question « comment déclarer un paramètre optionnel ? » ne
        retrouverait plus le passage dont c'est précisément le titre. On écrit donc
        le fil d'Ariane commun en tête, et le titre relatif de chaque sous-section
        à l'endroit où elle commence.
        """
        paths = [b.heading_path for b in bs]
        common: tuple[str, ...] = ()
        if paths:
            for i in range(min(len(p) for p in paths)):
                if len({p[i] for p in paths}) == 1:
                    common += (paths[0][i],)
                else:
                    break
        heading = " > ".join(common)
        parts: list[str] = []
        previous: tuple[str, ...] | None = None
        for b in bs:
            if b.heading_path != previous and len(b.heading_path) > len(common):
                parts.append("## " + " > ".join(b.heading_path[len(common):]))
            previous = b.heading_path
            parts.append(b.text)
        body = "\n\n".join(parts)
        return heading, (f"{heading}\n\n{body}" if heading else body)

    def emit(bs: list[_Block]) -> None:
        if not bs:
            return
        heading, full = render(bs)
        pos = len(chunks)
        chunks.append(Chunk(doc_id, f"{doc_id}#{pos:03d}", full, heading, pos))

    def budget(bs: list[_Block]) -> int:
        return count_tokens(render(bs)[1])

    for block in blocks:
        # On teste le budget RENDU, pas le texte brut : le fil d'Ariane s'ajoute au
        # passage et compte dans la limite du modèle. Le tester sur le texte seul
        # laisse passer les blocs qui débordent une fois le titre ajouté — c'est
        # la troisième fois que ce projet rencontre cette erreur de comptage.
        if budget([block]) > chunk_size:
            emit(cur)
            cur = []
            # On réserve de la place pour le fil d'Ariane, qui sera ajouté ensuite.
            marge = count_tokens(" > ".join(block.heading_path)) + 4
            for piece in _hard_split(block.text, chunk_size - marge, count_tokens):
                emit([_Block(piece, block.heading_path)])
            continue

        if cur and budget(cur + [block]) > chunk_size:
            emit(cur)
            # Recouvrement : on repart des derniers blocs, dans la limite d'`overlap`.
            carry: list[_Block] = []
            for b in reversed(cur):
                if sum(count_tokens(x.text) for x in [b, *carry]) > overlap:
                    break
                carry.insert(0, b)
            # Le recouvrement ne doit jamais faire dépasser le budget : sinon le
            # passage suivant serait tronqué par le modèle d'embedding — exactement
            # la perte d'information que le recouvrement est censé empêcher.
            while carry and budget([*carry, block]) > chunk_size:
                carry.pop(0)
            cur = carry
        cur.append(block)

    emit(cur)
    return chunks
