# Réflexion

Ce que ce projet m'a appris au-delà du code. Chaque affirmation est mesurée sur mes propres données ; les commandes de reproduction sont indiquées.

---

## 1. Mon banc mesurait ma plomberie, pas la sécurité

Première mesure d'injection indirecte : **0 % de réussite sur 12 attaques**. J'ai failli l'écrire dans le rapport. Elle était fausse pour deux raisons cumulées, dont aucune ne provoquait d'erreur.

**Le marquage était au niveau du document.** Un document empoisonné produit cinq passages dont un seul porte la charge. Mon indicateur « poison récupéré » se déclenchait dès qu'un passage *quelconque* du document remontait — l'attaque I08 était comptée comme livrée avec « 3 passages empoisonnés récupérés », alors que **le passage contenant réellement la charge n'était pas dans le top-5**.

**Et l'insertion tombait à côté.** Je m'ancrais sur le dernier paragraphe du passage récupéré, puis j'insérais *après* lui : la charge atterrissait en tête du passage **suivant**, celui que la recherche ne ramène pas. La charge était bien dans le document, le document bien marqué, tout paraissait correct, et le modèle ne l'a jamais vue.

Le correctif n'a pas été une meilleure heuristique. J'en ai essayé trois — après l'ancrage, avant, en tête de passage — et chacune livrait 7 à 9 charges sur 12, **jamais les mêmes** :

| Stratégie d'insertion | Charges livrées |
|---|---|
| après le paragraphe d'ancrage | 8/12 |
| avant le paragraphe d'ancrage | 7/12 |
| en tête de passage | 7/12 |
| **boucle de convergence** | **11/12** |

La cause est structurelle : **insérer du texte déplace les frontières de découpage**, donc le passage récupéré *après* insertion n'est pas celui qui l'était *avant*. Aucune position choisie sur l'index propre ne peut garantir la livraison sur l'index empoisonné. C'est un problème de point fixe, pas de choix d'emplacement.

D'où la construction itérative : insérer, réindexer, vérifier ce qui est réellement récupéré, recommencer pour les manquantes. Convergence en trois tours : 6, puis 9, puis 11 sur 12.

> **La leçon** : la question « mon banc mesure-t-il ce qu'il prétend mesurer ? » ne se répond pas par le raisonnement. Elle se répond par un contrôle qui tourne à chaque exécution. `rpib verify-poison` affiche le rang du passage porteur de chaque charge, et il doit précéder toute mesure d'ASR.
>
> Reproduction : `make ingest && uv run rpib verify-poison`

---

## 2. La vulnérabilité suit la capacité

Le résultat central, et il contredit l'intuition qu'un modèle plus petit serait plus sûr.

| | `qwen2.5:3b` | `qwen2.5:7b` |
|---|---|---|
| Injections **directes** | 62,5 % | 62,5 % |
| Injections **indirectes** | **0 %** | **16,7 %** |
| Exactitude sur 20 questions | 80 % | **100 %** |

Les attaques directes réussissent à l'identique — exactement les mêmes cinq sur huit. Elles ne dépendent donc pas de la taille du modèle mais de la formulation du prompt.

Les indirectes passent de zéro à deux **avec la seule augmentation de capacité**. Et ce ne sont pas n'importe lesquelles : ce sont les deux charges qui exigent de comprendre puis d'exécuter une consigne composée — exfiltration par image markdown (OWASP LLM05) et divulgation des autres documents du contexte (LLM02).

> **La leçon** : le 3B ne résiste pas, il ne comprend pas. Sa sécurité est de l'incompétence, pas de l'alignement, et elle disparaît exactement au moment où le modèle devient assez capable pour être utile. Il est à 80 % d'exactitude ; le 7B est à 100 %. « Prendre un petit modèle pour être plus sûr » revient à échanger de la sécurité contre de l'inutilité.
>
> Reproduction : `uv run rpib attack --collection poisoned --prompt-style naive --model qwen2.5:7b-instruct`

---

## 3. Un prompt de tâche bien spécifié est déjà une défense

Mon premier « baseline » n'en était pas un : il imposait déjà un format, la citation des sources, et une conduite à tenir en l'absence d'information. J'ai ajouté en amont le prompt réellement naïf, celui des tutoriels — « réponds à la question en te basant sur le contexte fourni ».

| Prompt système | ASR direct |
|---|---|
| tutoriel | **62,5 %** |
| tâche spécifiée | **50,0 %** |

Douze points et demi, sans aucun mécanisme de sécurité, sans coût de latence, sans faux positif. Et l'exactitude monte au passage de 80 % à 87 %.

> **La leçon** : une partie de ce qu'on attribue aux « garde-fous » relève en réalité de l'hygiène de prompt. Mesurer les défenses à partir d'un prompt déjà durci sous-estime le risque de départ, et surestime l'apport des couches suivantes. Le point de comparaison doit être le système que les gens écrivent réellement.

---

## 4. Le filtrage par mots-clés : du coût sans bénéfice

J'ai implémenté la détection heuristique (D3) **pour mesurer son insuffisance**, pas pour m'en servir. Le résultat est plus net que prévu.

| Configuration | ASR | Exactitude | Abstention à tort |
|---|---|---|---|
| +D2 nettoyage | 25 % | 87 % | 13 % |
| **+D3 heuristique** | **25 %** | **87 %** | **13 %** |

**Aucun effet mesurable sur l'ASR.** Et 13 % de questions légitimes laissées sans réponse — sur un corpus de documentation technique qui contient naturellement « ignorez », « système », « instruction ».

Trois attaques du jeu sont conçues pour le traverser, et elles le traversent : charge en base64 (aucun motif ne matche un texte encodé), charge répartie sur deux passages (chacun inoffensif isolément, le filtre analyse un passage à la fois), fait faux sans aucune instruction (un filtre orienté « instruction » ne peut pas le voir).

> **La leçon** : le filtrage par mots-clés est une liste noire face à un problème de confusion de canaux. On ne corrige pas un défaut d'architecture avec une regex. Les couches qui ont produit un effet sont celles qui changent la structure — le classifieur sur le contexte (D4) et la validation de sortie (D5).

---

## 5. Vingt attaques ne suffisent pas, et je le dis avant qu'on me le demande

L'ablation descend de 25 % à 10 %. C'est une tendance cohérente sur sept configurations successives. Ce n'est **pas** un résultat statistiquement significatif.

| Configuration | ASR | IC 95 % (Wilson) |
|---|---|---|
| prompt de tutoriel | 25 % | **11 % – 47 %** |
| toutes défenses | 10 % | **3 % – 30 %** |

Les intervalles se chevauchent largement. Avec vingt attaques, **une seule bascule déplace le taux de cinq points** — c'est précisément ce qui explique les remontées apparentes de D2 et D3, qui sont du bruit à une attaque près.

> **La leçon** : publier des pourcentages nus sur un échantillon de vingt aurait été de la mise en scène. Le rapport porte désormais l'intervalle sur chaque ligne. La correction réelle serait d'élargir le jeu à 60-100 scénarios ; c'est la première limite du travail, et elle est écrite dans le README.

---

## 6. Trois bugs de comptage que rien ne signalait

`multilingual-e5-small` tronque au-delà de 512 tokens **sans lever d'erreur**. La fin du passage n'est simplement jamais indexée. Sur ma première indexation, **13 passages sur 664 dépassaient la limite**.

Trois causes distinctes, découvertes l'une après l'autre :

1. le découpage de dernier recours vidait son tampon **après** dépassement, donc chaque morceau débordait d'une phrase ;
2. le recouvrement s'ajoutait au bloc suivant **sans revérifier le budget** — 64 + 512 = 576 ;
3. le test de dépassement portait sur le texte brut, **sans compter le fil d'Ariane** ajouté ensuite.

Aucune ne provoque d'exception. Seul un test d'invariant les attrape :

```python
def test_invariant_de_budget_avec_un_compteur_exact():
    for taille in (40, 80, 160, 320):
        chunks = split_markdown(long_doc, "d", chunk_size=taille, ...)
        assert max(mots(c.text) for c in chunks) <= taille
```

> **La leçon** : dans un pipeline de RAG, les bugs les plus coûteux ne lèvent pas d'erreur, ils dégradent silencieusement une métrique. La parade n'est pas la relecture, c'est l'invariant exécutable. Même chose pour la version accentuée d'un même piège : mes questions déclencheuses étaient écrites sans accents, et le modèle multilingue ne récupérait pas les mêmes passages — sans le moindre signal.
>
> Reproduction : `make test` puis `uv run rpib ingest` (le compteur `depassements_512` est affiché à chaque indexation).

---

## 7. Une hypothèse de performance démentie par la mesure

Ollama détectait `n_threads = 2` sur 12 — mon i7-1265U est un CPU hybride et la détection ne compte que les cœurs P. J'étais convaincu qu'imposer le bon nombre de threads accélérerait tout.

| `num_thread` | Génération | Traitement du prompt |
|---|---|---|
| 2 | 14,4 tok/s | 41,9 tok/s |
| 6 | 14,4 tok/s | 32,9 tok/s |
| 10 | 15,1 tok/s | 41,6 tok/s |

**Aucun gain.** Le goulot est la bande passante mémoire, pas le parallélisme. Je n'ai donc rien ajouté au code, et j'ai gardé la mesure dans le journal.

> **La leçon** : une optimisation non mesurée est une superstition. Le résultat utile ici n'est pas un gain de vitesse, c'est de savoir que ce levier n'existe pas sur cette machine — et donc que le banc complet coûte quatre heures, ce qui a dicté toute l'organisation des mesures.

---

## 8. Un chiffre de sécurité ne se lit jamais seul

Un système qui refuse de répondre à tout affiche 0 % d'injection réussie. C'est pour ça que chaque ligne du banc porte l'ASR **et** l'exactitude.

Sur le 7B, les défenses éliminent **la totalité** des injections indirectes réussies — et coûtent **13 points d'exactitude** (100 % → 87 %), soit environ 2,6 questions sur 20. C'est le prix, il est réel, et le lecteur doit le voir en même temps que le bénéfice.

Deuxième nuance, plus gênante : toutes couches activées, **4 attaques directes sur 8 réussissent encore** sur le 7B. Séparer instruction et donnée protège le canal des documents ; le canal de l'utilisateur reste, structurellement, celui des instructions.

> **La leçon** : le métier consiste à arbitrer une courbe de compromis, pas à atteindre zéro. Un projet qui annonce 0 % d'injection réussie a soit cassé son utilité, soit filtré sa propre métrique. J'ai d'ailleurs verrouillé ce dernier point par un test : la validation de sortie **ne doit pas** chercher les canaris du banc, sinon la mesure devient circulaire.

---

## 9. Ce que je ferais avec plus de temps

**Élargir le jeu d'attaques à 60-100 scénarios.** C'est la limite n°1 : sans cela, aucune couche n'est individuellement significative.

**Remesurer la latence.** `total_duration` d'Ollama inclut le chargement du modèle en mémoire ; quand deux modèles se disputent la place, la latence mesurée est multipliée par dix sans que le système évalué ait changé. Corrigé dans le code, mais les chiffres publiés précèdent le correctif — la colonne est donc absente du README.

**Une recherche hybride BM25 + dense avec re-ranking**, et le gain de Recall mesuré. Le Recall@5 au niveau du passage plafonne à 80 %.

**Un workflow d'intégration continue qui rejoue le banc et échoue si l'ASR régresse.** Traiter la régression de sécurité comme un test unitaire est ce qui distingue un banc d'essai d'une expérience ponctuelle.

**Un troisième modèle d'une autre famille.** Mes deux modèles sont des Qwen : je ne peux pas séparer l'effet de la taille de celui de l'entraînement.

---

## Ce que ce projet n'est pas

Ce n'est **pas une évaluation de la sécurité de FastAPI** : la documentation sert de corpus, elle n'est pas le sujet.

Ce n'est **pas un outil offensif**. Les charges sont inertes par construction : les canaris sont des chaînes sans effet, les URL d'exfiltration pointent vers le TLD réservé `attacker.test`, aucun outil n'est branché et aucune commande n'est exécutée. Le projet démontre des *canaux* pour les mesurer et les réduire, pas des dégâts.

Ce n'est **pas un résultat généralisable**. Un seul corpus, une seule langue, deux modèles de la même famille, vingt attaques. Ce qui est réutilisable, c'est la méthode et le code — pas les pourcentages.

Ce n'est **pas une preuve que les défenses fonctionnent**. C'est une mesure, avec ses intervalles de confiance, de ce qu'elles ont fait sur ce périmètre-là.
