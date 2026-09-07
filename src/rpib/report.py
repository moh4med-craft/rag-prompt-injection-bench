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
        # L'écart doit dépasser le rayon de l'extrémité arrondie, sinon
        # l'étiquette semble collée à la barre.
        ax.text(v * 100 + 4.5, i, f"{v:.0%}", va="center", ha="left",
                fontsize=9.5, color=t["muted"], zorder=4)

    ax.set_yticks(list(y), labels, fontsize=9.5, color=t["text"])
    ax.set_xlim(0, 128)
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


def make_figure(rows: list[dict], nom: str, panneaux: list[tuple],
                mode: str = "light") -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = THEMES[mode]
    labels = [r["label"] for r in rows]
    n = len(panneaux)
    fig, axes = plt.subplots(1, n, figsize=(4.4 + 3.3 * n, 0.62 * len(rows) + 2.1),
                             sharey=True)
    axes = axes if n > 1 else [axes]
    fig.patch.set_facecolor(t["surface"])
    for ax in axes:
        ax.set_facecolor(t["surface"])

    for ax, (cle, titre, sous_titre, role) in zip(axes, panneaux):
        _panel(ax, labels, [r[cle] for r in rows], t[role], titre, sous_titre, t)
    for ax in axes[1:]:
        ax.tick_params(labelleft=False)

    fig.subplots_adjust(left=0.24 if n > 2 else 0.17, right=0.98, top=0.84,
                        bottom=0.16, wspace=0.08)
    out = ROOT / "results" / "figures" / f"{nom}_{mode}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=170, facecolor=t["surface"])
    plt.close(fig)
    return out


def make_markdown(rows: list[dict]) -> Path:
    def ligne(r: dict) -> str:
        ic = r.get("asr_ic95", [0.0, 0.0])
        return (
            f"| `{r.get('model', '?').replace('qwen2.5:', '')}` | {r['label']} | "
            f"**{r['asr']:.0%}** | {ic[0]:.0%}–{ic[1]:.0%} | {r['asr_direct']:.0%} | "
            f"{r['asr_indirect']:.0%} | **{r['exactitude']:.0%}** | "
            f"{r['abstention_a_tort']:.0%} |"
        )

    lignes = [
        "# Résultats",
        "",
        ("Deux modèles · 20 questions annotées dont 5 sans réponse · 20 scénarios "
         "d'injection dont 11 charges indirectes effectivement livrées · température 0."),
        "",
        "## Ablation des défenses",
        "",
        ("| Modèle | Configuration | ASR | IC 95 % | directes | indirectes | "
         "Exactitude | Abstention à tort |"),
        "|---|---|---|---|---|---|---|---|",
    ]
    lignes += [ligne(r) for r in rows]

    def extremes(taille: str) -> tuple[dict, dict] | None:
        sel = [r for r in rows if taille in r.get("model", "")]
        debut_ = next((r for r in sel if r["label"] == "prompt de tutoriel"), None)
        fin_ = next((r for r in sel if r["label"] == "+D5 sortie"), None)
        return (debut_, fin_) if debut_ and fin_ else None

    lignes += ["", "## Bilan par modèle", ""]
    for taille, nom in (("3b", "qwen2.5:3b"), ("7b", "qwen2.5:7b")):
        paire = extremes(taille)
        if not paire:
            continue
        a, b = paire
        lignes.append(
            f"- **{nom}** — ASR {a['asr']:.0%} → {b['asr']:.0%} "
            f"(indirectes {a['asr_indirect']:.0%} → {b['asr_indirect']:.0%}), "
            f"exactitude {a['exactitude']:.0%} → {b['exactitude']:.0%}."
        )

    lignes += [
        "",
        "## Attaques encore réussies, toutes défenses activées",
        "",
    ]
    for taille, nom in (("3b", "qwen2.5:3b"), ("7b", "qwen2.5:7b")):
        paire = extremes(taille)
        if paire:
            lignes.append(f"- **{nom}** : {', '.join('`' + a + '`' for a in paire[1]['reussies']) or 'aucune'}")

    non_livrees = rows[0].get("non_livrees", [])
    if non_livrees:
        lignes += [
            "",
            "## Attaques non livrées",
            "",
            ("Charges dont le passage porteur n'a jamais été récupéré. Elles n'ont "
             "pas été *bloquées* : elles n'ont pas eu lieu. Les compter comme des "
             "succès défensifs surestimerait les défenses."),
            "",
        ] + [f"- `{a}`" for a in non_livrees]

    out = ROOT / "results" / "report.md"
    out.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return out


# La couleur suit la GRANDEUR mesurée, pas le panneau : les deux taux d'injection
# partagent la même teinte parce qu'ils mesurent la même chose sur deux
# sous-ensembles. Couple validé en clair et en sombre (séparation CVD et contraste).
PANNEAUX_ABLATION = [
    ("asr", "Injections réussies", "sur 20 scénarios · plus bas = mieux", "asr"),
    ("exactitude", "Exactitude", "sur 20 questions annotées · plus haut = mieux", "util"),
]
PANNEAUX_MODELES = [
    ("asr", "Injections réussies", "les 20 scénarios · plus bas = mieux", "asr"),
    ("asr_indirect", "dont indirectes", "12 charges dans le corpus", "asr"),
    ("exactitude", "Exactitude", "20 questions · plus haut = mieux", "util"),
]


def build_all() -> tuple[Path, list[Path]]:
    rows = json.loads((ROOT / "results" / "bench.json").read_text(encoding="utf-8"))
    ablation = [r for r in rows if "3b" in r.get("model", "")]

    # Comparaison de modèles : mêmes deux extrêmes, deux modèles. Un graphique
    # qui mélangerait les modèles sans les distinguer serait trompeur.
    def extremes(taille: str) -> list[dict]:
        sel = [r for r in rows if taille in r.get("model", "")]
        garde = [r for r in sel if r["label"] in ("prompt de tutoriel", "+D5 sortie")]
        for r in garde:
            r = dict(r)
        return [{**r, "label": f"{taille.upper()} — " +
                 ("sans défense" if "tutoriel" in r["label"] else "toutes défenses")}
                for r in garde]

    modeles = extremes("3b") + extremes("7b")

    figures = []
    for mode in ("light", "dark"):
        figures.append(make_figure(ablation, "ablation", PANNEAUX_ABLATION, mode))
        figures.append(make_figure(modeles, "modeles", PANNEAUX_MODELES, mode))
    return make_markdown(rows), figures
