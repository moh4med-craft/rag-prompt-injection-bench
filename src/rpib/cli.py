"""Interface en ligne de commande du banc d'essai."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import ROOT, Config, Defenses
from .llm import LLMClient, LLMError

app = typer.Typer(add_completion=False, help="Banc d'essai d'injection de prompt sur un RAG.")
console = Console()


def _config(defenses: str | None = None, model: str | None = None,
            collection: str = "clean", top_k: int | None = None,
            prompt_style: str = "spec") -> Config:
    from dataclasses import replace

    cfg = Config(collection=collection, prompt_style=prompt_style)
    if model:
        cfg = replace(cfg, llm_model=model)
    if top_k:
        cfg = replace(cfg, top_k=top_k)
    if defenses:
        noms = {d.strip() for d in defenses.split(",") if d.strip()}
        inconnus = noms - set(Defenses.__dataclass_fields__)
        if inconnus:
            raise typer.BadParameter(
                f"défense(s) inconnue(s) : {sorted(inconnus)}. "
                f"Attendu parmi {sorted(Defenses.__dataclass_fields__)}"
            )
        cfg = cfg.with_defenses(**{n: True for n in noms})
    return cfg


@app.command()
def health() -> None:
    """Vérifie que le modèle de langage et la base vectorielle répondent."""
    cfg = Config()
    try:
        console.print(f"[green]LLM[/green]        {LLMClient(cfg).health()}")
    except LLMError as exc:
        console.print(f"[red]LLM[/red]        {exc}")
    import chromadb

    client = chromadb.PersistentClient(path=cfg.chroma_path)
    cols = client.list_collections()
    if not cols:
        console.print("[yellow]Chroma[/yellow]     aucune collection — lance `rpib ingest`")
    for c in cols:
        console.print(f"[green]Chroma[/green]     {c.name} : {c.count()} passages")


@app.command()
def ingest(
    corpus: Path = typer.Option(ROOT / "corpus" / "clean", help="Répertoire de documents .md"),
    collection: str = typer.Option("clean", help="Nom de la collection Chroma"),
    sanitize: bool = typer.Option(False, help="Active D2 (nettoyage à l'ingestion)"),
    chunk_size: int = typer.Option(None, help="Surcharge la taille de passage"),
    overlap: int = typer.Option(None, help="Surcharge le recouvrement"),
) -> None:
    """Découpe, embedde et indexe un corpus."""
    from .store import Store

    cfg = Config(collection=collection)
    if chunk_size:
        cfg = Config(collection=collection, chunk_size=chunk_size)
    if overlap is not None:
        cfg = Config(collection=collection, chunk_size=cfg.chunk_size, chunk_overlap=overlap)

    console.print(f"Indexation de [cyan]{corpus}[/cyan] "
                  f"(chunk={cfg.chunk_size}, overlap={cfg.chunk_overlap}, D2={sanitize})")
    stats = Store(cfg, collection).build(corpus, sanitize=sanitize)
    table = Table(show_header=False)
    for k, v in stats.items():
        table.add_row(k, str(v))
    console.print(table)
    if stats["depassements_512"]:
        console.print(f"[red]{stats['depassements_512']} passages dépassent 512 tokens "
                      f"— ils seront tronqués silencieusement par le modèle.[/red]")


@app.command()
def ask(
    question: str,
    defenses: str = typer.Option(None, help="Ex. separation,sanitize,output_guard"),
    collection: str = typer.Option("clean"),
    model: str = typer.Option(None),
    top_k: int = typer.Option(None),
    show_prompt: bool = typer.Option(False, help="Affiche le prompt envoyé au modèle"),
    prompt_style: str = typer.Option("spec", help="naive (tutoriel) | spec (tâche spécifiée)"),
) -> None:
    """Pose une question au RAG."""
    from .pipeline import Pipeline

    cfg = _config(defenses, model, collection, top_k, prompt_style)
    answer = Pipeline(cfg).answer(question)

    if show_prompt:
        console.print("[dim]--- système ---[/dim]")
        console.print(answer.system)
        console.print("[dim]--- utilisateur ---[/dim]")
        console.print(answer.user)

    console.print(f"\n[bold]{answer.text}[/bold]\n")
    table = Table("#", "score", "document", "section")
    for i, r in enumerate(answer.retrieved, 1):
        table.add_row(str(i), f"{r.score:.3f}", r.doc_id[:40], (r.heading or "—")[:50])
    console.print(table)
    console.print(f"[dim]{answer.config_label} · {answer.latency_ms} ms · "
                  f"{answer.prompt_tokens} tokens de prompt[/dim]")


if __name__ == "__main__":
    app()


@app.command("eval-retrieval")
def eval_retrieval(
    collection: str = typer.Option("clean"),
    detail: bool = typer.Option(False, help="Affiche le rang obtenu pour chaque question"),
) -> None:
    """Mesure la récupération seule : Recall@k et MRR. Aucun appel au LLM."""
    from .eval_retrieval import evaluate_retrieval

    cfg = Config(collection=collection)
    rapport = evaluate_retrieval(cfg)

    table = Table("métrique", "niveau passage", "niveau document")
    for nom, strict, souple in rapport.as_rows():
        table.add_row(nom, strict, souple)
    console.print(table)
    if rapport.non_resolus:
        console.print(f"[yellow]Citations introuvables dans l'index : "
                      f"{rapport.non_resolus} — exclues du calcul.[/yellow]")
    if detail:
        d = Table("id", "rang passage", "rang doc", "document top-1", "document attendu")
        for r in rapport.details:
            fmt = lambda v: str(v) if v else "[red]absent[/red]"
            ok = r["top1_doc"] == r["gold_doc"]
            d.add_row(r["id"], fmt(r["rang"]), fmt(r["rang_doc"]),
                      f"[green]{r['top1_doc']}[/green]" if ok else r["top1_doc"],
                      r["gold_doc"])
        console.print(d)


@app.command()
def sweep(
    corpus: Path = typer.Option(ROOT / "corpus" / "clean"),
    sizes: str = typer.Option("256,512,1024", help="Tailles de passage à comparer"),
    overlaps: str = typer.Option("0,0.1,0.2", help="Recouvrements, en fraction de la taille"),
) -> None:
    """Balaie (taille de passage x recouvrement) et mesure le Recall de chacun.

    On ne CHOISIT pas ces paramètres, on les MESURE. Le balayage ne coûte aucun
    appel au LLM : seulement une réindexation et une recherche par combinaison.
    """
    from .eval_retrieval import evaluate_retrieval
    from .store import Store

    tailles = [int(s) for s in sizes.split(",")]
    fractions = [float(o) for o in overlaps.split(",")]
    resultats = []

    for taille in tailles:
        for fraction in fractions:
            recouvrement = int(taille * fraction)
            nom = f"sweep_{taille}_{recouvrement}"
            cfg = Config(chunk_size=taille, chunk_overlap=recouvrement, collection=nom)
            store = Store(cfg, nom)
            stats = store.build(corpus)
            rapport = evaluate_retrieval(cfg, store)
            resultats.append({
                "taille": taille, "recouvrement": recouvrement,
                "passages": stats["chunks"], "tokens_moyen": stats["tokens_moyen"],
                "r1": rapport.recall[1], "r5": rapport.recall[5],
                "r5_doc": rapport.recall_doc[5], "mrr": rapport.mrr,
                "non_resolus": len(rapport.non_resolus),
            })
            console.print(f"  {nom}: R@5={rapport.recall[5]:.1%} MRR={rapport.mrr:.3f}")
            store.client.delete_collection(nom)

    table = Table("taille", "recouvr.", "passages", "R@1", "R@5", "R@5 doc", "MRR")
    meilleur = max(resultats, key=lambda r: (r["r5"], r["mrr"]))
    for r in resultats:
        gagnant = r is meilleur
        style = "bold green" if gagnant else ""
        table.add_row(*[f"[{style}]{v}[/{style}]" if style else v for v in (
            str(r["taille"]), str(r["recouvrement"]), str(r["passages"]),
            f"{r['r1']:.1%}", f"{r['r5']:.1%}", f"{r['r5_doc']:.1%}", f"{r['mrr']:.3f}",
        )])
    console.print(table)
    console.print(f"[bold]Meilleur : chunk_size={meilleur['taille']}, "
                  f"overlap={meilleur['recouvrement']}[/bold]")

    import json
    out = ROOT / "results" / "sweep_chunking.json"
    out.write_text(json.dumps(resultats, indent=2), encoding="utf-8")
    console.print(f"[dim]{out}[/dim]")


@app.command()
def poison() -> None:
    """Génère corpus/poisoned/ à partir du corpus propre et des scénarios d'attaque."""
    from .poison import build_poisoned_corpus

    rapport = build_poisoned_corpus()
    console.print(f"[green]Insérées au point de récupération[/green] : "
                  f"{rapport['insérées']}")
    if rapport["repli_titre"]:
        console.print(f"[yellow]Repli sur le titre (passage peut-être non récupéré)[/yellow] : "
                      f"{rapport['repli_titre']}")
    console.print(f"[cyan]Documents malveillants créés[/cyan] : "
                  f"{rapport['nouveaux_documents']}")
    console.print("[dim]Réindexe ensuite : rpib ingest --corpus corpus/poisoned "
                  "--collection poisoned[/dim]")


@app.command("eval-utility")
def eval_utility_cmd(
    condition: str = typer.Option("e2e", help="e2e | oracle | closed"),
    defenses: str = typer.Option(None),
    collection: str = typer.Option("clean"),
    model: str = typer.Option(None),
    judge: bool = typer.Option(True, help="Second avis par juge LLM"),
    prompt_style: str = typer.Option("spec", help="naive | spec"),
) -> None:
    """Mesure la génération : exactitude, abstention, citation."""
    from .eval_utility import evaluate_utility, records_to_dicts, summarize
    from .runs import write_records

    cfg = _config(defenses, model, collection, prompt_style=prompt_style)
    records = evaluate_utility(cfg, condition=condition, judge=judge)
    resume = summarize(records)
    chemin = write_records(f"utility_{condition}_{cfg.label}", records_to_dicts(records))

    table = Table("métrique", "valeur")
    for k, v in resume.items():
        table.add_row(k, f"{v:.1%}" if isinstance(v, float) else str(v))
    console.print(table)
    console.print(f"[dim]{chemin}[/dim]")


@app.command("attack")
def attack_cmd(
    defenses: str = typer.Option(None),
    collection: str = typer.Option("poisoned"),
    model: str = typer.Option(None),
    prompt_style: str = typer.Option("spec", help="naive | spec"),
) -> None:
    """Rejoue les 20 scénarios d'injection et mesure l'ASR."""
    from .eval_attacks import evaluate_attacks, records_to_dicts, summarize
    from .runs import write_records

    cfg = _config(defenses, model, collection, prompt_style=prompt_style)
    records = evaluate_attacks(cfg)
    resume = summarize(records)
    chemin = write_records(f"attacks_{cfg.label}", records_to_dicts(records))

    table = Table("id", "type", "OWASP", "technique", "poison récupéré", "succès")
    for r in records:
        table.add_row(r.id, r.type, r.owasp, r.technique[:28],
                      "oui" if r.poison_retrieved else "[yellow]non[/yellow]",
                      "[red]OUI[/red]" if r.success else "[green]non[/green]")
    console.print(table)
    console.print(f"\n[bold]ASR global {resume['asr']:.1%}[/bold]  ·  "
                  f"directes {resume['asr_direct']:.1%}  ·  "
                  f"indirectes {resume['asr_indirect']:.1%}")
    if resume["non_livrees"]:
        console.print(f"[yellow]Charges jamais récupérées (attaque non livrée, "
                      f"pas bloquée) : {resume['non_livrees']}[/yellow]")
    console.print(f"[dim]{chemin}[/dim]")


@app.command()
def bench(
    model: str = typer.Option(None, help="Surcharge le modèle (ex. qwen2.5:7b-instruct)"),
    judge: bool = typer.Option(True, help="Second avis par juge LLM"),
    only: str = typer.Option(None, help="Limite aux étiquettes contenant cette chaîne"),
) -> None:
    """Exécute l'ablation complète : ASR ET exactitude, pour chaque configuration."""
    import json
    from dataclasses import asdict

    from .bench import ABLATION, run_bench

    out = ROOT / "results" / "bench.json"

    def sauvegarde(rows) -> None:
        out.write_text(json.dumps([asdict(r) for r in rows], indent=2,
                                  ensure_ascii=False), encoding="utf-8")

    configs = [c for c in ABLATION if not only or only in c[0]]
    rows = run_bench(model=model, judge=judge, configs=configs,
                     progress=lambda m: console.print(f"[dim]{m}...[/dim]"),
                     on_row=sauvegarde)

    table = Table("configuration", "ASR", "ASR direct", "ASR indirect",
                  "exactitude", "abstention à tort", "latence")
    for r in rows:
        table.add_row(r.label, f"[red]{r.asr:.0%}[/red]", f"{r.asr_direct:.0%}",
                      f"{r.asr_indirect:.0%}", f"[green]{r.exactitude:.0%}[/green]",
                      f"{r.abstention_a_tort:.0%}", f"{r.latence_ms} ms")
    console.print(table)

    console.print(f"[dim]{out}[/dim]")


@app.command()
def report() -> None:
    """Produit results/report.md et les figures à partir de results/bench.json."""
    from .report import build_all

    md, figures = build_all()
    console.print(f"[green]{md}[/green]")
    for f in figures:
        console.print(f"[green]{f}[/green]")
