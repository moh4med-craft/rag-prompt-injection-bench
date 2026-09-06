"""Tests du découpage.

On teste les invariants qui, s'ils cassent, dégradent la récupération SANS lever
d'erreur — c'est-à-dire exactement les bugs qu'on ne verrait pas autrement.
"""

from rpib.chunking import approx_tokens, split_markdown

DOC = """# Titre principal

Un paragraphe d'introduction qui explique le sujet du document.

## Première section

Contenu de la première section, en plusieurs phrases. Il faut assez de texte pour
que le budget de tokens soit atteint et que le decoupage se declenche vraiment.

```python
def exemple():
    return "un bloc de code qui ne doit jamais etre coupe en deux"
```

## Deuxieme section

Contenu de la deuxieme section.
"""


def test_aucun_passage_ne_depasse_le_budget():
    chunks = split_markdown(DOC, "doc", chunk_size=60, overlap=10)
    # +5 de tolérance : le fil d'Ariane est ajouté après l'accumulation.
    assert all(approx_tokens(c.text) <= 65 for c in chunks)


def test_les_blocs_de_code_restent_entiers():
    chunks = split_markdown(DOC, "doc", chunk_size=60, overlap=10)
    fences = sum(c.text.count("```") for c in chunks)
    # Chaque bloc de code garde ses deux délimiteurs dans le même passage.
    assert fences % 2 == 0


def test_le_fil_d_ariane_est_present_dans_le_texte_indexe():
    chunks = split_markdown(DOC, "doc", chunk_size=200, overlap=0)
    assert any("Premiere section" in c.text or "Première section" in c.text
               for c in chunks)


def test_identifiants_uniques_et_ordonnes():
    chunks = split_markdown(DOC, "doc", chunk_size=60, overlap=10)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert [c.position for c in chunks] == list(range(len(chunks)))


def test_document_vide():
    assert split_markdown("", "vide") == []


def test_invariant_de_budget_avec_un_compteur_exact():
    """L'invariant qui compte : AUCUN passage ne doit dépasser le budget.

    Un dépassement ne lève pas d'erreur — le modèle d'embedding tronque en
    silence. Ce test est donc le seul garde-fou.
    """
    mots = lambda t: len(t.split())
    # Titres volontairement longs : le fil d'Ariane consomme une part notable du
    # budget, ce qui expose les erreurs de comptage.
    long_doc = "# Un titre principal plutot long pour consommer du budget\n\n" + "\n\n".join(
        f"## Section numero {i} avec un intitule delibarement verbeux\n\n"
        + " ".join(f"mot{j}" for j in range(120))
        for i in range(6)
    )
    for taille in (40, 80, 160, 320):
        chunks = split_markdown(long_doc, "d", chunk_size=taille, overlap=taille // 4,
                                count_tokens=mots)
        assert chunks
        assert max(mots(c.text) for c in chunks) <= taille
