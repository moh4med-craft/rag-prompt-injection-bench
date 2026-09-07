"""Génération du rapport final : tableau d'ablation et figure.

Contrainte de conception : la figure met côte à côte SÉCURITÉ et UTILITÉ, dans
deux panneaux partageant les mêmes lignes. Un graphique du seul ASR raconterait
une histoire fausse — celle d'une amélioration monotone — en cachant ce que
chaque couche coûte en exactitude. Le lecteur doit voir les deux d'un seul regard.

Deux fichiers sont produits, clair et sombre : une figure à fond blanc devient un
rectangle blanc dans un README lu en thème sombre.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT

# Palette validée (séparation CVD et contraste vérifiés dans les deux modes).
THEMES = {
    "light": {"surface": "#fcfcfb", "text": "#0b0b0b", "muted": "#52514e",
              "grid": "#e3e2de", "asr": "#e34948", "util": "#2a78d6"},
    "dark": {"surface": "#1a1a19", "text": "#ffffff", "muted": "#c3c2b7",
             "grid": "#383835", "asr": "#e66767", "util": "#3987e5"},
}


def _panel(ax, labels, valeurs, couleur, titre, sous_titre, t) -> None:
    y = range(len(labels))
    for i, v in zip(y, valeurs):
        # Trait épais à extrémités arrondies : l'arrondi est calculé en espace
        # d'affichage, donc jamais déformé par l'échelle. Le bord gauche est
        # rogné par la limite d'axe à 0, ce qui l'ancre à la ligne de base.
        ax.plot([0, v * 100], [i, i], lw=13, solid_capstyle="round",
                color=couleur, zorder=3)
        ax.text(v * 100 + 2.5, i, f"{v:.0%}", va="center", ha="left",
                fontsize=9.5, color=t["muted"], zorder=4)

    ax.set_yticks(list(y), labels, fontsize=9.5, color=t["text"])
    ax.set_xlim(0, 118)
    ax.set_ylim(-0.7, len(labels) - 0.3)
    ax.invert_yaxis()
    ax.set_xticks([0, 50, 100], ["0", "50", "100 %"], fontsize=8.5, color=t["muted"])
    ax.xaxis.grid(True, color=t["grid"], lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for cote in ("top", "right", "left", "bottom"):
        ax.spines[cote].set_visible(False)
    ax.tick_params(length=0)
    ax.set_title(titre, fontsize=11, color=t["text"], loc="left", pad=14, weight="bold")
    ax.text(0, 1.015, sous_titre, transform=ax.transAxes, fontsize=8.5,
            color=t["muted"], ha="left", va="bottom")


def make_figure(rows: list[dict], mode: str = "light") -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = THEMES[mode]
    labels = [r["label"] for r in rows]
    fig, (g, d) = plt.subplots(1, 2, figsize=(11, 0.62 * len(rows) + 2.1), sharey=True)
    fig.patch.set_facecolor(t["surface"])
    for ax in (g, d):
        ax.set_facecolor(t["surface"])

    _panel(g, labels, [r["asr"] for r in rows], t["asr"],
           "Injections réussies (ASR)", "sur 20 scénarios · plus bas = mieux", t)
    _panel(d, labels, [r["exactitude"] for r in rows], t["util"],
           "Exactitude", "sur 20 questions annotées · plus haut = mieux", t)
    d.tick_params(labelleft=False)

    fig.subplots_adjust(left=0.17, right=0.98, top=0.84, bottom=0.16, wspace=0.08)
    out = ROOT / "results" / "figures" / f"ablation_{mode}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=170, facecolor=t["surface"])
    plt.close(fig)
    return out


def make_markdown(rows: list[dict]) -> Path:
    base, final = rows[0], rows[-1]
    lignes = [
        "# Résultats",
        "",
        (f"Modèle : `{base.get('model', 'qwen2.5:3b-instruct')}` · "
         "20 questions annotées · 20 scénarios d'injection · température 0."),
        "",
        "## Ablation des défenses",
        "",
        ("| Configuration | ASR | ASR directes | ASR indirectes | Exactitude | "
         "Abstention à tort | Latence |"),
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lignes.append(
            f"| {r['label']} | **{r['asr']:.0%}** | "
            f"{r['asr_ic95'][0]:.0%}–{r['asr_ic95'][1]:.0%} | "
            f"{r['asr_direct']:.0%} | {r['asr_indirect']:.0%} | "
            f"**{r['exactitude']:.0%}** | {r['abstention_a_tort']:.0%} | "
            f"{r['latence_ms']} ms |"
        )
    delta_asr = base["asr"] - final["asr"]
    delta_util = base["exactitude"] - final["exactitude"]
    lignes += [
        "",
        (f"**Bilan** : ASR {base['asr']:.0%} → {final['asr']:.0%} "
         f"({delta_asr:+.0%}), exactitude {base['exactitude']:.0%} → "
         f"{final['exactitude']:.0%} ({-delta_util:+.0%})."),
        "",
        "## Attaques encore réussies avec toutes les défenses",
        "",
    ]
    lignes += ([f"- `{a}`" for a in final["reussies"]] or ["- aucune"])
    if final["non_livrees"]:
        lignes += [
            "",
            "## Attaques non livrées",
            "",
            ("Charges dont le passage malveillant n'a jamais été récupéré. Elles "
             "n'ont pas été *bloquées* : elles n'ont pas eu lieu. Les compter comme "
             "des succès défensifs surestimerait les défenses."),
            "",
        ] + [f"- `{a}`" for a in final["non_livrees"]]

    out = ROOT / "results" / "report.md"
    out.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return out


def build_all() -> tuple[Path, list[Path]]:
    rows = json.loads((ROOT / "results" / "bench.json").read_text(encoding="utf-8"))
    return make_markdown(rows), [make_figure(rows, m) for m in ("light", "dark")]
