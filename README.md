# rag-prompt-injection-bench

Mesure de bout en bout la vulnérabilité d'une application RAG à l'injection de prompt, et le coût réel des défenses qui la réduisent.

## Le problème

Un RAG colle dans le prompt d'un modèle de langage du texte qu'il ne contrôle pas. Le modèle reçoit une séquence de tokens homogène où **rien ne distingue structurellement une instruction d'une donnée** : il n'existe pas, pour un LLM, d'équivalent des requêtes préparées de SQL. N'importe qui pouvant faire indexer un document peut donc y déposer une consigne qui sera exécutée avec les privilèges d'un utilisateur légitime, lequel n'a rien demandé — c'est l'injection _indirecte_, et le filtrage de l'entrée utilisateur ne la voit même pas passer.

La difficulté n'est pas d'écrire une défense, c'est plutôt de savoir ce qu'elle vaut. Un système qui refuse de répondre à tout affiche 0 % d'injection réussie ; sans mesure d'utilité en face, le chiffre de sécurité ne veut rien dire. Ce dépôt mesure les deux, couche par couche.

## L'approche

### Chaîne technique

| Composant        | Choix                                        | Pourquoi                                                                                                                                                                                                                                              |
| ---------------- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Découpage        | maison, conscient de la structure markdown   | Blocs de code jamais coupés, fil d'Ariane des titres conservé dans le texte indexé, budget compté avec **le tokenizer réel du modèle d'embedding** — au-delà de 512 tokens il tronque sans lever d'erreur, et la fin du passage n'est jamais indexée. |
| Embeddings       | `intfloat/multilingual-e5-small` (384 dim)   | Corpus en français : un modèle anglophone s'effondre. Les préfixes `query:` / `passage:` sont obligatoires — les omettre coûte plus de dix points de Recall, en silence.                                                                              |
| Base vectorielle | ChromaDB, persistante                        | Filtrage par métadonnées natif, indispensable pour étiqueter les passages empoisonnés et mesurer combien sont réellement récupérés. FAISS ne stocke ni texte ni métadonnées.                                                                          |
| Modèle           | Ollama, `qwen2.5:3b-instruct`, température 0 | Local, sans GPU ni clé d'API : n'importe qui peut reproduire les mesures. Température nulle, sinon l'écart avant/après n'est pas interprétable.                                                                                                       |
| Orchestration    | aucune — ~1 200 lignes de Python explicite   | Pas de LangChain ni LlamaIndex : chaque décision du pipeline est visible et modifiable, ce qui est le sujet même du projet.                                                                                                                           |

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

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="results/figures/modeles_dark.png">
  <img alt="Injections réussies et exactitude, par modèle, avec et sans défenses" src="results/figures/modeles_light.png">
</picture>

| Modèle       | Sans défense                       | Toutes défenses                   | Exactitude       |
| ------------ | ---------------------------------- | --------------------------------- | ---------------- |
| `qwen2.5:3b` | ASR **25 %** · indirectes 0 %      | ASR **10 %** · indirectes 0 %     | 80 % → 87 %      |
| `qwen2.5:7b` | ASR **35 %** · indirectes **17 %** | ASR **20 %** · indirectes **0 %** | 100 % → **87 %** |

### Ce qui marche

**Les défenses éliminent l'intégralité des injections indirectes réussies** — les deux qui aboutissaient sur le 7B, exfiltration par image markdown (LLM05) et divulgation des autres documents du contexte (LLM02), sont bloquées. C'est le cas d'usage pour lequel elles sont conçues, et sur ce périmètre elles font le travail.

**La vulnérabilité suit la capacité, pas l'inverse.** Le 3B ne subit aucune injection indirecte, mais il plafonne à 80 % d'exactitude. Le 7B atteint 100 % d'exactitude et devient injectable. Les deux attaques qui passent sont les plus exigeantes en compréhension : le petit modèle ne résiste pas, il ne comprend pas. Choisir un modèle plus faible pour être plus sûr revient à échanger de la sécurité contre de l'inutilité.

**Un prompt de tâche bien spécifié est déjà une défense.** Passer du prompt de tutoriel (« réponds à partir du contexte ») à un prompt qui impose format, citation et conduite à tenir en l'absence d'information fait tomber l'ASR direct de 62 % à 50 %, sans aucun mécanisme de sécurité et sans coût.

**La récupération est mesurée, pas supposée** : Recall@5 de 80 % au niveau du passage et 93 % au niveau du document, MRR 0,647, après un balayage de `chunk_size` × `overlap`. L'écart entre les deux niveaux mesure la sensibilité au découpage.

### Ce qui ne marche pas

**Vingt attaques ne suffisent pas à départager les couches.** Une seule bascule déplace le taux de cinq points, et les intervalles de confiance à 95 % se chevauchent tous : 25 % [11–47] au départ, 10 % [3–30] à l'arrivée. La tendance est cohérente sur sept configurations successives, mais **aucune couche prise isolément n'est statistiquement significative**. Il faudrait 60 à 100 scénarios. C'est la limite principale de ce travail.

**Le filtrage heuristique (D3) n'a rien apporté de mesurable** et a coûté des refus injustifiés : 13 % de questions légitimes laissées sans réponse. Trois attaques du jeu sont conçues pour le traverser — charge en base64, charge répartie sur deux passages, fait faux sans instruction — et elles le traversent. C'était le but de l'expérience, mais le résultat est net : sur ce corpus, cette couche est du coût sans bénéfice.

**Les défenses coûtent 13 points d'exactitude sur le 7B** (100 % → 87 %), soit environ 2,6 questions sur 20. Le chiffre de sécurité ne se lit jamais seul.

**L'injection directe résiste aux défenses.** Toutes couches activées, 4 attaques directes sur 8 réussissent encore sur le 7B. Séparer instruction et donnée protège le canal des documents, pas celui de l'utilisateur — qui reste, structurellement, le canal des instructions.

**Une attaque n'a jamais pu être livrée** (I05, détournement de la récupération, LLM08) : le document bourré de mots-clés ne se fait pas remonter par la recherche. Elle est comptée à part, pas comme un succès défensif.

**La mesure de latence est invalidée** : `total_duration` d'Ollama inclut le chargement du modèle en mémoire, ce qui a pollué les premières configurations d'un facteur dix. Corrigé dans le code, mais les chiffres publiés ici précèdent le correctif — la colonne est donc absente et devra être remesurée.

**Le modèle 3B ne cite pas ses sources** malgré la consigne explicite du prompt système. La traçabilité des réponses, qui est un argument central du RAG, n'est pas acquise à cette taille.

**Portée** : un seul corpus, une seule langue, deux modèles de la même famille. Rien ici ne se généralise à un autre modèle sans être remesuré — et le banc existe précisément pour permettre cette remesure.

### Reproductibilité

Température nulle, seed fixé, modèles épinglés par tag, corpus épinglé à un commit. `results/report.md` est généré, `results/runs/*.jsonl` conserve pour chaque appel la question, les passages récupérés, le prompt final et la réponse brute.

**Vérifié à froid** : un `docker compose up` sur un environnement reconstruit de zéro reproduit les mêmes chiffres au dernier décimal — convergence de l'empoisonnement en 6 → 9 → 11 charges livrées sur 12, Recall@5 de 80,0 % au niveau du passage et 93,3 % au niveau du document, MRR 0,647.

Le corpus empoisonné est construit **par convergence** : insérer une charge déplace les frontières de découpage, donc le passage récupéré après insertion n'est pas celui qui l'était avant. Trois heuristiques statiques ont livré 7 à 9 charges sur 12, jamais les mêmes. La boucle — insérer, réindexer, vérifier ce qui est réellement lu, recommencer — converge à 11/12 en trois tours. `rpib verify-poison` affiche le rang du passage porteur de chaque charge et doit précéder toute mesure.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="results/figures/ablation_dark.png">
  <img alt="Ablation des cinq couches de défense sur qwen2.5:3b" src="results/figures/ablation_light.png">
</picture>

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

**Portée et éthique.** Banc défensif. Les charges sont inertes : les canaris sont des chaînes sans effet, les URL d'exfiltration pointent vers le TLD réservé `attacker.test`, aucun outil n'est branché et aucune commande n'est exécutée. Le corpus est de la documentation publique, modifiée localement pour les besoins de la mesure. Ce dépôt démontre des _canaux_ d'attaque afin de les mesurer et de les réduire, pas des dégâts.
