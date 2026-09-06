# Journal de mesures

Notes brutes au fil de l'eau. Ce fichier n'est pas le rapport : il garde ce qui a
été observé, y compris ce qui a raté, parce qu'on ne s'en souvient plus deux jours après.

## Environnement

- CPU : Intel i7-1265U (2 cœurs P + 8 cœurs E, 12 threads), 30 Go de RAM, **pas de GPU**
- Python 3.12 via `uv` (le système est en 3.14, trop récent pour les wheels ML)
- `torch 2.14.0+cpu` — index PyTorch CPU épinglé, ~2,5 Go de dépendances CUDA évitées
- Ollama 0.33.3, `qwen2.5:3b-instruct` (mesure principale), `qwen2.5:7b-instruct` (disponible)

## Corpus

Documentation française de FastAPI, `fastapi@50113da` — 123 documents, 820 Ko de markdown.
Récupéré par `scripts/fetch_corpus.sh`, donc reproductible.

## Découpage (chunk_size=512, overlap=64)

| | |
|---|---|
| Passages | 664 (avant nettoyage des ancres MkDocs) |
| Tokens par passage, moyenne | 440 |
| Dépassements de 512 tokens | 13 → **0 après correction** |

Trois erreurs de comptage successives ont produit des passages au-dessus du budget :
1. `_hard_split` vidait son tampon APRÈS dépassement → chaque morceau débordait d'une phrase ;
2. le recouvrement s'ajoutait au bloc suivant sans revérifier le budget (64 + 512 = 576) ;
3. le test de dépassement portait sur le texte brut, sans compter le fil d'Ariane ajouté ensuite.

Aucune ne lève d'erreur : le modèle d'embedding tronque en silence. Seul un test
d'invariant les attrape. C'est la leçon la plus transférable du projet.

## Récupération (20 questions annotées, 15 répondables)

| Métrique | Niveau passage | Niveau document |
|---|---|---|
| Recall@1 | 53,3 % | 66,7 % |
| Recall@3 | 66,7 % | 86,7 % |
| Recall@5 | **80,0 %** | **100,0 %** |
| MRR | 0,627 | 0,797 |

L'écart de 20 points entre les deux niveaux mesure la sensibilité au découpage :
le bon document est toujours trouvé, le bon passage une fois sur cinq ne l'est pas.
Échecs au niveau passage : q07, q08, q09.

## Génération

Première réponse de bout en bout correcte (question q01, ordre des chemins).
**Le modèle 3B ne cite pas ses sources** malgré la consigne explicite du prompt
système — à mesurer et à reporter, c'est une limite du petit modèle.

## Performance CPU — mesure, pas intuition

Ollama détectait `n_threads = 2` sur 12 (CPU hybride Intel : seuls les cœurs P
sont comptés). Hypothèse : forcer `num_thread` accélérerait. **Mesure :**

| num_thread | génération | traitement du prompt |
|---|---|---|
| 2 | 14,4 tok/s | 41,9 tok/s |
| 4 | 13,0 tok/s | 44,5 tok/s |
| 6 | 14,4 tok/s | 32,9 tok/s |
| 8 | 14,1 tok/s | 38,5 tok/s |
| 10 | 15,1 tok/s | 41,6 tok/s |

**Aucun gain.** Le goulot est la bande passante mémoire, pas le parallélisme. Le
réglage n'a donc pas été ajouté. Conséquence directe sur le projet : un prompt de
2 000 tokens coûte ~50 s de traitement, ce qui fixe le banc complet à environ 4 h.

## Empoisonnement

12 attaques indirectes placées : 11 insérées au point de récupération exact,
1 document malveillant créé de toutes pièces (I05, attaque de la base vectorielle).

Premier essai : I08 retombait sur un repli. Cause trouvée — **les questions
déclencheuses étaient écrites sans accents** alors que les questions annotées en
ont. Le modèle multilingue ne récupère pas la même chose. Corrigé en reprenant
verbatim les questions du jeu annoté, ce qui est aussi un meilleur design : leur
comportement de récupération est déjà mesuré.

## Sécurité — à compléter

*(banc en cours)*
