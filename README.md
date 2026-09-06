# rag-prompt-injection-bench

Mesure de bout en bout la vulnérabilité d'une application RAG à l'injection de prompt, et le coût réel des défenses qui la réduisent.

## Le problème

Un RAG colle dans le prompt d'un modèle de langage du texte qu'il ne contrôle pas. Le modèle reçoit une séquence de tokens homogène où **rien ne distingue structurellement une instruction d'une donnée** : il n'existe pas, pour un LLM, d'équivalent des requêtes préparées de SQL. N'importe qui pouvant faire indexer un document peut donc y déposer une consigne qui sera exécutée avec les privilèges d'un utilisateur légitime, lequel n'a rien demandé — c'est l'injection *indirecte*, et le filtrage de l'entrée utilisateur ne la voit même pas passer.

La difficulté n'est pas d'écrire une défense, c'est de savoir ce qu'elle vaut. Un système qui refuse de répondre à tout affiche 0 % d'injection réussie ; sans mesure d'utilité en face, le chiffre de sécurité ne veut rien dire. Ce dépôt mesure les deux, couche par couche.

## L'approche

### Chaîne technique

| Composant | Choix | Pourquoi |
|---|---|---|
| Découpage | maison, conscient de la structure markdown | Blocs de code jamais coupés, fil d'Ariane des titres conservé dans le texte indexé, budget compté avec **le tokenizer réel du modèle d'embedding** — au-delà de 512 tokens il tronque sans lever d'erreur, et la fin du passage n'est jamais indexée. |
| Embeddings | `intfloat/multilingual-e5-small` (384 dim) | Corpus en français : un modèle anglophone s'effondre. Les préfixes `query:` / `passage:` sont obligatoires — les omettre coûte plus de dix points de Recall, en silence. |
| Base vectorielle | ChromaDB, persistante | Filtrage par métadonnées natif, indispensable pour étiqueter les passages empoisonnés et mesurer combien sont réellement récupérés. FAISS ne stocke ni texte ni métadonnées. |
| Modèle | Ollama, `qwen2.5:3b-instruct`, température 0 | Local, sans GPU ni clé d'API : n'importe qui peut reproduire les mesures. Température nulle, sinon l'écart avant/après n'est pas interprétable. |
| Orchestration | aucune — ~1 200 lignes de Python explicite | Pas de LangChain ni LlamaIndex : chaque décision du pipeline est visible et modifiable, ce qui est le sujet même du projet. |

### Architecture

```
                    corpus/clean/  ──┐
                    (123 documents)  │
                                     ▼
                            ┌────────────────┐
   data/attacks.jsonl ─────►│  poison.py     │  insère chaque charge à l'endroit
   (20 scénarios)           │                │  exact que la question récupère
                            └───────┬────────┘
                                    ▼
                          corpus/poisoned/
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
      [D2] nettoyage à l'ingestion                (sans nettoyage)
              └─────────────────────┬─────────────────────┘
                                    ▼
                    découpage → embeddings → ChromaDB
                                    │
   question ──► embedding ──► recherche des k plus proches
                                    │
                                    ▼
                    [D3] heuristique  ──► passage écarté
                    [D4] classifieur  ──► passage écarté
                                    │
                                    ▼
                    [D1] contexte balisé et marqué ligne à ligne
                                    │
                                    ▼
                                  LLM
                                    │
                                    ▼
                    [D5] validation de la sortie
                                    │
                                    ▼
                                 réponse
```

### Méthode de mesure

Trois décisions font la valeur — ou la nullité — d'un chiffre de sécurité.

**Le succès d'une attaque est détecté par canari.** Chaque charge demande l'émission d'une chaîne inerte et unique (`PWNED-a1b2`), absente du corpus et impossible à produire par hasard. Le verdict est une recherche de motif : objectif, automatique, sans juge LLM dans la boucle de mesure. Un test verrouille le corollaire — **la validation de sortie ne cherche pas ces canaris**, sinon l'ASR tomberait à zéro pour une raison circulaire.

**Les charges indirectes sont insérées à l'endroit exact que la question récupère.** On interroge l'index propre pour savoir quel passage la question déclencheuse ramène, puis on y greffe la charge. Sans cela, une attaque échouerait parce que son passage n'est jamais lu — ce qui n'est pas une victoire de la défense, et gonflerait artificiellement son efficacité. Les cas non livrés sont comptés séparément.

**Les questions déclencheuses sont reprises verbatim du jeu annoté.** Leur comportement de récupération est donc déjà mesuré, et la réponse sous attaque se compare à une réponse de référence.

### Le jeu d'évaluation

20 questions annotées à la main, dont **5 sans réponse dans le corpus** : la bonne réponse y est une abstention, ce qui mesure la résistance à l'hallucination. L'annotation désigne une **citation verbatim**, jamais un identifiant de passage — un identifiant deviendrait faux dès qu'on modifie la taille de découpage, c'est-à-dire au moment précis où l'on balaie ce paramètre pour le choisir. Des tests vérifient que chaque citation existe réellement dans le document annoté.

20 scénarios d'injection, 8 directes et 12 indirectes, couvrant huit catégories du Top 10 OWASP for LLM Applications (LLM01 injection, LLM02 divulgation, LLM04 empoisonnement, LLM05 traitement de la sortie, LLM06 agentivité excessive, LLM07 fuite du prompt système, LLM08 faiblesses des embeddings, LLM09 désinformation). Trois d'entre elles sont conçues pour traverser le filtrage par mots-clés : charge en base64, charge découpée sur deux passages, fait faux sans aucune instruction.

## Résultat

*(en cours de mesure — cette section sera remplie par les chiffres du banc)*

## Lancer le projet

```
docker compose up
```

Le service récupère le corpus, télécharge le modèle, construit les trois index (propre, empoisonné, empoisonné puis nettoyé), mesure la récupération, puis démontre une injection indirecte avec et sans défense. Le banc complet se lance à part :

```
docker compose run --rm app bench
```

Sans Docker : `make setup && make corpus && make ingest && make bench && make report`.

---

**Portée et éthique.** Banc défensif. Les charges sont inertes : les canaris sont des chaînes sans effet, les URL d'exfiltration pointent vers le TLD réservé `attacker.test`, aucun outil n'est branché et aucune commande n'est exécutée. Le corpus est de la documentation publique, modifiée localement pour les besoins de la mesure. Ce dépôt démontre des *canaux* d'attaque afin de les mesurer et de les réduire, pas des dégâts.
