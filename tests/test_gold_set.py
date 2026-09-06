"""Tests du jeu de questions annoté.

Un jeu d'évaluation faux est pire qu'une absence d'évaluation : il produit des
chiffres crédibles et faux. Ces tests vérifient l'annotation elle-même — que
chaque citation de référence existe bien, verbatim, dans le document annoté.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus" / "clean"
GOLD = [json.loads(l) for l in (ROOT / "data" / "qa_gold.jsonl").read_text(
    encoding="utf-8").splitlines() if l.strip()]


def test_taille_et_composition():
    assert len(GOLD) == 20
    assert sum(1 for q in GOLD if q["answerable"]) == 15
    # Les questions sans réponse ne sont pas un remplissage : elles mesurent
    # l'abstention, c'est-à-dire la résistance à l'hallucination.
    assert sum(1 for q in GOLD if not q["answerable"]) == 5


def test_identifiants_uniques():
    ids = [q["id"] for q in GOLD]
    assert len(ids) == len(set(ids))


@pytest.mark.skipif(not CORPUS.exists(), reason="corpus absent (scripts/fetch_corpus.sh)")
@pytest.mark.parametrize("q", [q for q in GOLD if q["answerable"]], ids=lambda q: q["id"])
def test_la_citation_de_reference_existe_verbatim(q):
    doc = CORPUS / f"{q['gold_doc']}.md"
    assert doc.exists(), f"{q['id']} : document annoté introuvable ({doc.name})"
    assert q["gold_evidence"] in doc.read_text(encoding="utf-8"), (
        f"{q['id']} : citation absente de {doc.name} — annotation à corriger"
    )


@pytest.mark.parametrize("q", [q for q in GOLD if q["answerable"]], ids=lambda q: q["id"])
def test_la_reponse_de_reference_contient_ses_propres_mots_cles(q):
    """Cohérence interne : si la référence ne satisfait pas son propre critère,
    le critère est faux."""
    ref = q["answer_ref"].lower()
    for mot in q["must_include"]:
        assert mot.lower() in ref, f"{q['id']} : '{mot}' absent de la réponse de référence"


@pytest.mark.skipif(not CORPUS.exists(), reason="corpus absent")
@pytest.mark.parametrize("q", [q for q in GOLD if not q["answerable"]], ids=lambda q: q["id"])
def test_les_questions_sans_reponse_le_sont_vraiment(q):
    """Garde-fou : si le corpus évolue, une question « sans réponse » peut le devenir."""
    assert q["answer_ref"] is None and q["gold_doc"] is None


ATTAQUES = [json.loads(l) for l in (ROOT / "data" / "attacks.jsonl").read_text(
    encoding="utf-8").splitlines() if l.strip()]
QUESTIONS = {q["id"]: q["question"] for q in GOLD}


def test_composition_du_jeu_d_attaques():
    assert len(ATTAQUES) == 20
    assert sum(1 for a in ATTAQUES if a["type"] == "direct") == 8
    assert sum(1 for a in ATTAQUES if a["type"] == "indirect") == 12
    # Huit des dix catégories du Top 10 OWASP for LLM sont représentées.
    assert len({a["owasp"] for a in ATTAQUES}) >= 8


def test_canaris_uniques_et_absents_du_corpus():
    canaris = [a["canary"] for a in ATTAQUES if a["canary"]]
    assert len(canaris) == len(set(canaris)), "un canari partagé rend le verdict ambigu"
    if CORPUS.exists():
        corpus = " ".join(f.read_text(encoding="utf-8") for f in CORPUS.glob("*.md"))
        for c in canaris:
            assert c not in corpus, f"{c} apparaît dans le corpus : faux positifs garantis"


@pytest.mark.parametrize("a", [a for a in ATTAQUES if a["type"] == "indirect"],
                         ids=lambda a: a["id"])
def test_la_question_declencheuse_vient_du_jeu_annote(a):
    """Une question déclencheuse improvisée récupère autre chose que ce qu'on a
    mesuré — et l'attaque échoue alors pour une raison sans rapport avec la
    sécurité. Elles sont donc reprises verbatim du jeu annoté."""
    assert a["gold_id"] in QUESTIONS, f"{a['id']} : pas de question annotée liée"
    assert a["trigger_question"] == QUESTIONS[a["gold_id"]]
