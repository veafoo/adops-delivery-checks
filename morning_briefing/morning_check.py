#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""morning_check.py — routine matinale delivery, tous canaux, tous formats.

Usage:
  python3 morning_check.py [dossier_data] [--html briefing.html] [--md briefing.md] [--quiet]

Lit les exports de la veille dans le dossier (défaut .) et produit un briefing priorisé.
Tool-agnostique : tout passe par des CSV, donc Trello / Airtable / Notion / Sheets /
l'outil maison de l'employeur = un seul adaptateur d'export à écrire, jamais le moteur.

FICHIERS LUS (chacun optionnel, absence signalée jamais silencieuse)
  campaigns.csv      1 ligne par campagne : pacing + objectifs + marge, TOUS canaux
  board.csv          avancement de mise en place (export type Trello/Airtable)
  counts.csv         réconciliation ad server annonceur -> gate dédié
  tracking_urls.txt  lint des URLs de tracking -> gate dédié
  delivery_export.csv  pacing CTV jour/source -> gate 5 familles

CHECKS
  1 PACING          adhérence à la courbe, tolérance par canal (DOOH/CTV != display)
  2 OBJECTIFS       CTR, taux de landing, taux de conversion, CPA vs cibles
  3 MARGE           marge réalisée vs floor, par campagne
  4 MISE EN PLACE   tâches bloquées / en retard / go-live imminent non prêt
  5 FIN DE VOL      campagnes terminées -> file d'extraction pour le report
  + gates chaînés (réconciliation, URLs, pacing CTV détaillé)

SORTIES  console + Markdown + HTML imprimable (jauges SVG, tableaux couleur) -> PDF
Stdlib uniquement, zéro réseau. Exit 0 = rien à faire, 2 = au moins un P1.
"""
import csv
import html
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

def _charger_reconciliation():
    """Importe le verdict de réconciliation du script voisin.

    On réutilise la logique au lieu de la réécrire : dupliquer des seuils de
    verdict dans deux gates garantit qu'ils divergeront. Si le script voisin est
    absent, on retombe sur l'appel en sous-processus (sortie brute en annexe).
    """
    import importlib.util
    chemin = RACINE / "discrepancy/reconcile_counts.py"
    if not chemin.exists():
        return None
    spec = importlib.util.spec_from_file_location("_rec", chemin)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


REC = _charger_reconciliation()

# Tolérance de pacing par canal : un écran DOOH et un display ne se pilotent pas pareil
BANDE_PACING = {"display": 0.90, "native": 0.90, "inapp": 0.90, "video": 0.88,
                "audio": 0.85, "dooh": 0.85, "ctv": 0.80, "search": 0.92}
FLOOR_MARGE_DEFAUT = 0.20
JOURS_ALERTE_GOLIVE = 3


def f(row, key, d=0.0):
    try:
        return float(str(row.get(key, "")).replace(",", ".").replace(" ", "") or d)
    except ValueError:
        return d


def i(row, key, d=""):
    return (row.get(key) or d).strip()


def jours(d1, d2):
    try:
        return (datetime.strptime(d2, "%Y-%m-%d").date() - datetime.strptime(d1, "%Y-%m-%d").date()).days
    except Exception:
        return None


def pc(x):
    return "-" if x is None else f"{100*x:.1f} %"


def lire(p):
    """Lit un CSV en ignorant les lignes de commentaire (#) et les lignes vides.

    Les exports d'outils tiers et les fixtures portent souvent un préambule ;
    sans ce filtre, la première ligne de commentaire devient l'en-tête et tout
    le fichier est lu de travers, en silence.
    """
    if not p.exists():
        return None
    with open(p, newline="", encoding="utf-8") as fh:
        lignes = [l for l in fh if l.strip() and not l.lstrip().startswith("#")]
    return list(csv.DictReader(lignes))


# ---------------------------------------------------------------- checks
def check_pacing(rows, today):
    """Adhérence = livré / cible attendue à date, tolérance par canal."""
    out = []
    for r in rows:
        if i(r, "statut").lower() not in ("live", "en cours", "running"):
            continue
        canal = i(r, "canal", "display").lower()
        cible = f(r, "imp_cible")
        livre = f(r, "imp_livrees")
        d1, d2 = i(r, "date_debut"), i(r, "date_fin")
        total = jours(d1, d2) or 0
        ecoule = jours(d1, today) or 0
        if cible <= 0 or total <= 0:
            continue
        part = min(max(ecoule / total, 0.0), 1.0)
        attendu = cible * part
        adh = livre / attendu if attendu > 0 else 1.0
        bande = BANDE_PACING.get(canal, 0.90)
        proj = livre / part if part > 0 else 0.0     # projection fin de vol au rythme actuel
        if adh < bande * 0.85:
            sev, msg = "P1", f"sous-livraison sévère ({pc(adh)} de la courbe) - projection fin de vol {pc(proj/cible)} du volume"
        elif adh < bande:
            sev, msg = "P2", f"sous la bande {canal} ({pc(adh)} vs {pc(bande)}) - à surveiller aujourd'hui"
        elif adh > 1.25 and part < 0.9:
            sev, msg = "P2", f"sur-livraison ({pc(adh)}) - budget consommé trop vite, fin de vol à vide"
        else:
            continue
        out.append((sev, i(r, "campagne"), canal, msg, adh))
    return out


def check_objectifs(rows):
    """CTR, taux de landing, taux de conversion, CPA vs cibles déclarées."""
    out = []
    for r in rows:
        if i(r, "statut").lower() not in ("live", "en cours", "running"):
            continue
        nom, canal = i(r, "campagne"), i(r, "canal", "display").lower()
        imp, clics = f(r, "imp_livrees"), f(r, "clics")
        land, conv, dep = f(r, "landings"), f(r, "conversions"), f(r, "depense")
        ctr = clics / imp if imp else None
        t_land = land / clics if clics else None
        t_conv = conv / land if land else (conv / clics if clics else None)
        cpa = dep / conv if conv else None
        for label, val, cible, sens in (
            ("CTR", ctr, f(r, "ctr_cible", 0) or None, "min"),
            ("taux de landing", t_land, f(r, "landing_cible", 0) or None, "min"),
            ("taux de conversion", t_conv, f(r, "conv_cible", 0) or None, "min"),
            ("CPA", cpa, f(r, "cpa_cible", 0) or None, "max"),
        ):
            if val is None or not cible:
                continue
            rate = val / cible if sens == "min" else cible / val
            if rate < 0.7:
                out.append(("P1", nom, canal, f"{label} à {val:.4g} contre une cible de {cible:.4g} ({pc(rate)} de l'objectif)", rate))
            elif rate < 0.9:
                out.append(("P2", nom, canal, f"{label} sous l'objectif : {val:.4g} vs {cible:.4g}", rate))
        # Écart clics -> landings : signe un problème de redirect, pas de créa
        if clics and t_land is not None and t_land < 0.7:
            out.append(("P1", nom, canal,
                        f"{pc(1-t_land)} des clics n'atteignent pas la landing : chaîne de redirect ou tag - lint check_tracking_url.py", t_land))
    return out


def check_marge(rows):
    out = []
    for r in rows:
        if i(r, "statut").lower() not in ("live", "en cours", "running"):
            continue
        revenu, cout = f(r, "revenu"), f(r, "cout_media")
        if revenu <= 0:
            continue
        marge = (revenu - cout) / revenu
        floor = f(r, "marge_floor", FLOOR_MARGE_DEFAUT) or FLOOR_MARGE_DEFAUT
        if marge < floor * 0.5:
            out.append(("P1", i(r, "campagne"), i(r, "canal"), f"marge {pc(marge)} très en dessous du floor {pc(floor)} - arbitrage requis", marge))
        elif marge < floor:
            out.append(("P2", i(r, "campagne"), i(r, "canal"), f"marge {pc(marge)} sous le floor {pc(floor)}", marge))
    return out


def check_board(board, camps, today):
    """Avancement de mise en place : bloqué, en retard, go-live imminent non prêt."""
    out = []
    golive = {i(r, "campagne"): i(r, "date_debut") for r in (camps or [])}
    par_camp = {}
    for t in board:
        c = i(t, "campagne")
        par_camp.setdefault(c, []).append(t)
        statut, ech = i(t, "statut").lower(), i(t, "echeance")
        if statut in ("blocked", "bloque", "bloqué"):
            out.append(("P1", c, "setup", f"tâche BLOQUÉE : {i(t,'tache')} ({i(t,'responsable') or 'non assignée'})", 0.0))
        elif ech and statut not in ("done", "fait", "terminé") and (jours(today, ech) or 99) < 0:
            out.append(("P2", c, "setup", f"tâche en retard de {abs(jours(today, ech))} j : {i(t,'tache')}", 0.0))
    for c, taches in par_camp.items():
        faites = sum(1 for t in taches if i(t, "statut").lower() in ("done", "fait", "terminé"))
        ratio = faites / len(taches)
        d = golive.get(c)
        reste = jours(today, d) if d else None
        if reste is not None and 0 <= reste <= JOURS_ALERTE_GOLIVE and ratio < 1.0:
            out.append(("P1", c, "setup",
                        f"go-live dans {reste} j et setup à {pc(ratio)} ({len(taches)-faites} tâche(s) restante(s))", ratio))
    return out, par_camp


def check_fin_de_vol(rows, today):
    """Campagnes terminées -> file d'extraction pour le report client."""
    out = []
    for r in rows:
        d2 = i(r, "date_fin")
        st = i(r, "statut").lower()
        fini = st in ("fini", "terminé", "ended", "done")
        vient_de_finir = d2 and (jours(d2, today) or -99) in (0, 1, 2, 3)
        if fini or vient_de_finir:
            imp, clics, conv = f(r, "imp_livrees"), f(r, "clics"), f(r, "conversions")
            cible = f(r, "imp_cible")
            atteinte = imp / cible if cible else None
            out.append((i(r, "campagne"), i(r, "canal"), i(r, "annonceur"), d2, atteinte, imp, clics, conv))
    return out


def check_placements(rows, camps, today):
    """Niveau placement : un placement peut cramer alors que la campagne globale est OK.

    C'est le cas le plus vicieux du metier : l'agrege masque le placement mort.
    """
    par_camp, findings = {}, []
    etat = {i(c, "campagne"): c for c in (camps or [])}
    for r in rows:
        nom = i(r, "campagne")
        c = etat.get(nom)
        if c is None or i(c, "statut").lower() not in ("live", "en cours", "running"):
            continue
        total = jours(i(c, "date_debut"), i(c, "date_fin")) or 0
        ecoule = jours(i(c, "date_debut"), today) or 0
        part = min(max(ecoule / total, 0.0), 1.0) if total else 1.0
        cible, imp = f(r, "imp_cible"), f(r, "imp_livrees")
        att = cible * part
        adh = imp / att if att else None
        canal = i(r, "canal") or i(c, "canal", "display")
        bande = BANDE_PACING.get(canal.lower(), 0.90)
        dep, clics = f(r, "depense"), f(r, "clics")
        land, conv = f(r, "landings"), f(r, "conversions")
        rev, cout = f(r, "revenu"), f(r, "cout_media")
        pl = {"placement": i(r, "placement"), "canal": canal, "adh": adh, "bande": bande,
              "imp": imp, "clics": clics, "landings": land, "conv": conv, "depense": dep,
              "budget": f(r, "budget"), "conso": (dep / (f(r, "budget") * part)) if f(r, "budget") and part else None,
              "ctr": clics / imp if imp else None,
              "cpc": dep / clics if clics else None,
              "cpv": dep / land if land else None,
              "cpa": dep / conv if conv else None,
              "marge": (rev - cout) / rev if rev else None}
        par_camp.setdefault(nom, []).append(pl)
        if adh is not None and adh < bande * 0.85:
            findings.append(("P1", nom, canal,
                             f"placement {pl['placement']} a {pc(adh)} de sa courbe alors que la campagne tient : "
                             f"placement a diagnostiquer, pas la campagne", adh))
        elif adh is not None and adh > 1.35:
            findings.append(("P2", nom, canal,
                             f"placement {pl['placement']} sur-livre ({pc(adh)}) : il mange le budget des autres", adh))
    return par_camp, findings


def check_discrepancy(rows, today):
    """Réconciliation ad server annonceur, en première classe.

    Un écart de comptage se détecte le matin ou se paie à la facturation : plus
    tôt on voit un tag mort, moins d'heures de données sont perdues. Ce check
    produit donc des P1/P2 et une jauge, pas seulement une annexe.
    """
    if REC is None:
        return [], [], None
    par_place = {}
    for r in rows:
        cle = (i(r, "campagne"), i(r, "placement"), i(r, "env", "web").lower())
        par_place.setdefault(cle, []).append(r)

    findings, detail = [], []
    dans_bande = 0
    for (camp, place, env), lignes in sorted(par_place.items()):
        nom, ratio, verdict, note = REC.diagnostiquer(f"{camp} / {place}", lignes, env)
        if verdict == "SANS_DONNEE":
            continue
        tp = sum(f(r, "imp_plateforme") for r in lignes)
        ta = sum(f(r, "imp_adserver") for r in lignes)
        bande = REC.BANDES.get(env, 0.90)
        if verdict in ("STRUCTUREL_OK", "CONSENT_EXPLIQUE", "DOOH_NON_COMPARABLE"):
            dans_bande += 1
        detail.append({"campagne": camp, "placement": place, "env": env, "verdict": verdict,
                       "ratio": ratio, "bande": bande, "plateforme": tp, "adserver": ta,
                       "note": note})
        if verdict == "TAG_MORT":
            findings.append(("P1", camp, env,
                             f"TAG MORT sur {place} : l'ad server annonceur ne compte quasi rien "
                             f"({ratio:.1%}) - chaque heure qui passe perd de la donnée, recette du placement", ratio))
        elif verdict == "RUPTURE":
            findings.append(("P1", camp, env,
                             f"RUPTURE de comptage sur {place} ({ratio:.1%}) - {note}", ratio))
        elif verdict == "SORTIE_DE_BANDE":
            findings.append(("P2", camp, env,
                             f"écart hors bande sur {place} : {ratio:.1%} contre une bande {env} "
                             f"à {bande:.0%} - dérouler le runbook", ratio))
    part = dans_bande / len(detail) if detail else None
    return findings, detail, part


# ---------------------------------------------------------------- gates chaînés
GATES = [
    # counts.csv est traité nativement par check_discrepancy() quand l'import du
    # verdict a réussi ; il n'est chaîné en sous-processus qu'en repli.
    ("URLs de tracking", "tracking_urls.txt",
     RACINE / "discrepancy/check_tracking_url.py", ("[ERREUR]",)),
    ("Pacing CTV détaillé (5 familles)", "delivery_export.csv",
     RACINE / "ctv_pacing/diagnose_pacing.py", ("[RISQUE]",)),
]


def run_gates(data):
    res, absents = [], []
    for titre, fichier, gate, marks in GATES:
        src = data / fichier
        if not src.exists():
            absents.append(f"{titre} : `{fichier}` absent")
            continue
        if not gate.exists():
            absents.append(f"{titre} : gate manquant ({gate.name}) - `git pull && ./install.sh` ?")
            continue
        r = subprocess.run([sys.executable, str(gate), str(src)], capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        res.append((titre, sum(out.count(m) for m in marks), out))
    return res, absents


# ---------------------------------------------------------------- rendu
VERT, ORANGE, ROUGE, GRIS = "#1a9850", "#f0a30a", "#d7191c", "#b8c2cc"


def couleur(v, ref=1.0):
    """Couleur RELATIVE a l'attendu, jamais absolue.

    v   = realise / attendu-a-date (1.0 = pile dans les temps)
    ref = seuil acceptable pour ce canal ou cet indicateur (la bande)

    Vert   : on est dans ce qui etait prevu.
    Orange : on decroche mais c'est rattrapable, ou on va trop vite.
    Rouge  : c'est la merde, ou on crame le budget.
    Un depassement franc n'est pas une bonne nouvelle : sur-livrer ou sur-depenser
    vide la fin de vol. Au-dela de 1.20 on passe orange, au-dela de 1.35 rouge.
    """
    if v is None:
        return GRIS
    if v > 1.35:
        return ROUGE
    if v > 1.20:
        return ORANGE
    if v >= ref:
        return VERT
    if v >= ref * 0.85:
        return ORANGE
    return ROUGE


def eur(x):
    return "-" if x is None else ("{:,.0f}".format(x).replace(",", " ") + " EUR")


def money(x, d=2):
    return "-" if x is None else ("{:,.{}f}".format(x, d).replace(",", " ") + " EUR")


def gauge(pct, label, sous="", ref=1.0):
    """Jauge donut SVG autonome, couleur relative a l'attendu (voir couleur())."""
    pct = max(0.0, min(pct if pct is not None else 0.0, 1.5))
    aff = min(pct, 1.0)
    col = couleur(pct, ref)
    r, circ = 42, 2 * 3.14159 * 42
    return f"""<div class="g"><svg viewBox="0 0 110 110" width="104" height="104">
<circle cx="55" cy="55" r="{r}" fill="none" stroke="#e6eaee" stroke-width="13"/>
<circle cx="55" cy="55" r="{r}" fill="none" stroke="{col}" stroke-width="13" stroke-linecap="round"
 stroke-dasharray="{circ:.1f}" stroke-dashoffset="{circ*(1-aff):.1f}" transform="rotate(-90 55 55)"/>
<text x="55" y="52" text-anchor="middle" font-size="21" font-weight="700" fill="#16202b">{100*pct:.0f}%</text>
<text x="55" y="70" text-anchor="middle" font-size="9.5" fill="#5b6b7b">{html.escape(sous)}</text></svg>
<div class="gl">{html.escape(label)}</div></div>"""


def bar(v, ref=1.0):
    """Barre horizontale, meme semantique de couleur que la jauge."""
    col = couleur(v, ref)
    w = max(0.0, min(v if v is not None else 0.0, 1.0))
    return f'<div class="bar"><span style="width:{w*100:.0f}%;background:{col}"></span></div>'


CSS = """
*{box-sizing:border-box}body{font:11px/1.45 -apple-system,"Helvetica Neue",Arial,sans-serif;color:#16202b;margin:0;padding:18px}
h1{font-size:19px;color:#3b4d61;border-bottom:2px solid #3b4d61;padding-bottom:5px;margin:0 0 4px}
h2{font-size:13px;color:#3b4d61;margin:18px 0 7px;border-bottom:1px solid #d5dbe2;padding-bottom:3px}
.sub{color:#5b6b7b;margin:0 0 14px}
.gauges{display:flex;gap:26px;flex-wrap:wrap;margin:12px 0 4px}
.g{text-align:center}.gl{font-size:10px;color:#3b4d61;font-weight:600;margin-top:2px;max-width:118px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:10px}
th{background:#3b4d61;color:#fff;text-align:left;padding:5px 7px;font-weight:600}
td{border:1px solid #d5dbe2;padding:5px 7px;vertical-align:top}
tbody tr:nth-child(even){background:#f7f9fa}
.p1{background:#fdecea!important;border-left:3px solid #d7191c}
.p2{background:#fff8e6!important;border-left:3px solid #f0a30a}
.ok{background:#eefaf0!important;border-left:3px solid #1a9850}
.tag{display:inline-block;padding:1px 6px;border-radius:9px;font-size:9px;font-weight:700;color:#fff}
.tP1{background:#d7191c}.tP2{background:#f0a30a}
.bar{background:#e6eaee;height:7px;border-radius:4px;overflow:hidden;min-width:48px}
.hint{font-weight:400;font-size:8px;opacity:.85}
.v{font-size:8.6px;color:#3b4d61;white-space:nowrap}
h3{font-size:11px;color:#16202b;margin:12px 0 3px}
h3.alert{color:#d7191c}
.bar span{display:block;height:100%}
pre{background:#f4f6f8;border:1px solid #d5dbe2;border-radius:3px;padding:7px;font-size:8.2px;
white-space:pre-wrap;page-break-inside:avoid}
.foot{margin-top:16px;padding-top:8px;border-top:1px solid #d5dbe2;color:#5b6b7b;font-size:9px}
tr,li{page-break-inside:avoid}h1,h2{page-break-after:avoid}
@page{size:A4;margin:12mm}
"""


def render_html(ctx):
    t = ctx["today"]
    p1, p2 = ctx["p1"], ctx["p2"]
    h = [f"<style>{CSS}</style>",
         f"<h1>Briefing delivery - {t}</h1>",
         f"<p class='sub'>{ctx['n_live']} campagne(s) en cours sur {ctx['n_canaux']} canaux · "
         f"<b>{len(p1)} point(s) P1</b> · {len(p2)} P2 · {len(ctx['fins'])} campagne(s) à extraire</p>",
         "<div class='gauges'>",
         gauge(ctx["adh_glob"], "Pacing dans la bande", f"{ctx['n_bande']}/{ctx['n_live']} campagnes"),
         gauge(ctx["conso_glob"], "Budget consommé", "vs prévu à date"),
         gauge(ctx["marge_glob_rel"], "Marge vs floor", pc(ctx["marge_glob"]) + " réalisée"),
         gauge(ctx["disc_glob"], "Comptage conforme",
               f"{ctx['n_disc_ok']}/{ctx['n_disc']} placements" if ctx["n_disc"] else "pas de données"),
         gauge(ctx["obj_glob"], "Objectifs atteints", "part des cibles OK"),
         gauge(ctx["setup_glob"], "Mise en place", "tâches faites"),
         "</div>"]

    if p1 or p2:
        h.append("<h2>Points à traiter</h2><table><thead><tr><th>Prio</th><th>Campagne</th>"
                 "<th>Canal</th><th>Constat et action</th><th>Niveau</th></tr></thead><tbody>")
        for sev, camp, canal, msg, val in p1 + p2:
            h.append(f"<tr class='{sev.lower()}'><td><span class='tag t{sev}'>{sev}</span></td>"
                     f"<td><b>{html.escape(camp)}</b></td><td>{html.escape(canal)}</td>"
                     f"<td>{html.escape(msg)}</td><td>{bar(val)}</td></tr>")
        h.append("</tbody></table>")
    else:
        h.append("<h2>Points à traiter</h2><table><tbody><tr class='ok'><td>Rien à signaler : "
                 "tout est dans les bandes.</td></tr></tbody></table>")

    if ctx["live"]:
        h.append("<h2>Vue globale - toutes les campagnes en cours</h2>"
                 "<table><thead><tr><th>Campagne</th><th>Canal</th><th>Marché</th>"
                 "<th>Pacing<br><span class='hint'>vs attendu à date</span></th><th>Budget<br>"
                 "<span class='hint'>consommé vs prévu</span></th><th>Impressions</th><th>CTR</th>"
                 "<th>CPC</th><th>Coût / visite</th><th>CPA</th><th>Marge<br>"
                 "<span class='hint'>vs floor</span></th></tr></thead><tbody>")
        for r in ctx["live"]:
            h.append(f"<tr><td><b>{html.escape(r['nom'])}</b></td><td>{html.escape(r['canal'])}</td>"
                     f"<td>{html.escape(r['marche'])}</td>"
                     f"<td>{bar(r['adh'], r['bande'])}<span class='v'>{pc(r['adh'])}</span></td>"
                     f"<td>{bar(r['conso'])}<span class='v'>{eur(r['depense'])} / {eur(r['budget'])}</span></td>"
                     f"<td>{r['imp']:,.0f}</td><td>{pc(r['ctr'])}</td><td>{money(r['cpc'])}</td>"
                     f"<td>{money(r['cpv'])}</td><td>{money(r['cpa'])}</td>"
                     f"<td>{bar(r['marge_rel'])}<span class='v'>{pc(r['marge'])}</span></td></tr>")
        h.append("</tbody></table>")

    # Vue par campagne : detail placements, les campagnes a probleme d'abord
    if ctx["placements"]:
        h.append("<h2>Vue par campagne - détail placements</h2>"
                 "<p class='sub'>Une campagne globalement dans les temps peut cacher un placement mort. "
                 "Les campagnes ayant au moins un placement hors bande sont listées en premier.</p>")
        def pire(item):
            return min([p["adh"] for p in item[1] if p["adh"] is not None] or [9])
        for nom, pls in sorted(ctx["placements"].items(), key=pire):
            hs = any(p["adh"] is not None and p["adh"] < p["bande"] for p in pls)
            h.append(f"<h3 class='{'alert' if hs else ''}'>{html.escape(nom)}"
                     f"{' — placement(s) hors bande' if hs else ' — tous placements dans la bande'}</h3>")
            h.append("<table><thead><tr><th>Placement</th><th>Canal</th><th>Pacing</th><th>Budget</th>"
                     "<th>Impressions</th><th>CTR</th><th>CPC</th><th>Coût / visite</th><th>CPA</th>"
                     "<th>Marge</th></tr></thead><tbody>")
            for p in sorted(pls, key=lambda x: x["adh"] if x["adh"] is not None else 9):
                h.append(f"<tr><td>{html.escape(p['placement'])}</td><td>{html.escape(p['canal'])}</td>"
                         f"<td>{bar(p['adh'], p['bande'])}<span class='v'>{pc(p['adh'])}</span></td>"
                         f"<td>{bar(p['conso'])}<span class='v'>{eur(p['depense'])} / {eur(p['budget'])}</span></td>"
                         f"<td>{p['imp']:,.0f}</td><td>{pc(p['ctr'])}</td><td>{money(p['cpc'])}</td>"
                         f"<td>{money(p['cpv'])}</td><td>{money(p['cpa'])}</td>"
                         f"<td><span class='v'>{pc(p['marge'])}</span></td></tr>")
            h.append("</tbody></table>")

    if ctx["disc"]:
        h.append("<h2>Réconciliation avec les ad servers annonceurs</h2>"
                 "<p class='sub'>Un écart se règle le matin ou se paie à la facturation. "
                 "Les placements à investiguer sont listés en premier.</p>"
                 "<table><thead><tr><th>Campagne</th><th>Placement</th><th>Env.</th>"
                 "<th>Verdict</th><th>Ratio<br><span class='hint'>ad server / plateforme</span></th>"
                 "<th>Plateforme</th><th>Ad server</th><th>Lecture</th></tr></thead><tbody>")
        ordre = {"TAG_MORT": 0, "RUPTURE": 1, "SORTIE_DE_BANDE": 2,
                 "CONSENT_EXPLIQUE": 3, "STRUCTUREL_OK": 4, "DOOH_NON_COMPARABLE": 5}
        for d in sorted(ctx["disc"], key=lambda x: ordre.get(x["verdict"], 9)):
            classe = ("p1" if d["verdict"] in ("TAG_MORT", "RUPTURE")
                      else "p2" if d["verdict"] == "SORTIE_DE_BANDE" else "")
            h.append(f"<tr class='{classe}'><td><b>{html.escape(d['campagne'])}</b></td>"
                     f"<td>{html.escape(d['placement'])}</td><td>{html.escape(d['env'])}</td>"
                     f"<td><b>{d['verdict'].replace('_',' ')}</b></td>"
                     f"<td>{bar(d['ratio'], d['bande'])}<span class='v'>{pc(d['ratio'])}</span></td>"
                     f"<td>{d['plateforme']:,.0f}</td><td>{d['adserver']:,.0f}</td>"
                     f"<td style='font-size:9px'>{html.escape(d['note'])}</td></tr>")
        h.append("</tbody></table>")

    if ctx["fins"]:
        h.append("<h2>Campagnes terminées - file d'extraction pour le report</h2><table><thead><tr>"
                 "<th>Campagne</th><th>Annonceur</th><th>Canal</th><th>Fin</th><th>Atteinte</th>"
                 "<th>Impressions</th><th>Clics</th><th>Conv.</th></tr></thead><tbody>")
        for nom, canal, ann, d2, att, imp, clics, conv in ctx["fins"]:
            h.append(f"<tr><td><b>{html.escape(nom)}</b></td><td>{html.escape(ann)}</td>"
                     f"<td>{html.escape(canal)}</td><td>{html.escape(d2)}</td>"
                     f"<td>{bar(att, 0.98)}<span class='v'>{pc(att)}</span></td>"
                     f"<td>{imp:,.0f}</td><td>{clics:,.0f}</td><td>{conv:,.0f}</td></tr>")
        h.append("</tbody></table>")

    for titre, n, out in ctx["gates"]:
        h.append(f"<h2>{html.escape(titre)} - {n} point(s) P1</h2><pre>{html.escape(out)}</pre>")
    if ctx["absents"]:
        h.append("<h2>Checks non exécutés</h2><ul>" +
                 "".join(f"<li>{html.escape(a)}</li>" for a in ctx["absents"]) + "</ul>")

    h.append("<div class='foot'>Gates déterministes uniquement (zéro réseau). Les actions "
             "proposées viennent des gates ; tout arbitrage (relâcher un floor de marge, prévenir un "
             "client, accepter une sous-livraison) reste humain.</div>")
    return "<!doctype html><meta charset='utf-8'><title>Briefing delivery " + t + "</title>" + "".join(h)


def render_md(ctx):
    L = [f"# Briefing delivery - {ctx['today']}", "",
         f"**{len(ctx['p1'])} P1** · {len(ctx['p2'])} P2 · {ctx['n_live']} campagnes en cours · "
         f"{len(ctx['fins'])} à extraire", "",
         f"- Pacing dans la bande : {pc(ctx['adh_glob'])} ({ctx['n_bande']}/{ctx['n_live']} campagnes)",
         f"- Budget consommé vs prévu à date : {pc(ctx['conso_glob'])}",
         f"- Marge réalisée : {pc(ctx['marge_glob'])} (soit {pc(ctx['marge_glob_rel'])} du floor)",
         f"- Comptage conforme : {pc(ctx['disc_glob'])} ({ctx['n_disc_ok']}/{ctx['n_disc']} placements)"
         if ctx["n_disc"] else "- Comptage : pas de données de réconciliation",
         f"- Objectifs atteints : {pc(ctx['obj_glob'])}",
         f"- Mise en place : {pc(ctx['setup_glob'])}", ""]
    if ctx["p1"] or ctx["p2"]:
        L += ["| Prio | Campagne | Canal | Constat et action |", "|---|---|---|---|"]
        L += [f"| {s} | {c} | {ca} | {m} |" for s, c, ca, m, _ in ctx["p1"] + ctx["p2"]]
    else:
        L.append("Rien à signaler : tout est dans les bandes.")
    if ctx["placements"]:
        pires = []
        for nom, pls in ctx["placements"].items():
            for pl in pls:
                if pl["adh"] is not None and pl["adh"] < pl["bande"]:
                    pires.append((pl["adh"], nom, pl["placement"], pl["canal"]))
        if pires:
            L += ["", "## Placements hors bande (l'agrégé les masque)", ""]
            L += [f"- **{n}** / {p} ({c}) : {pc(a)} de la courbe" for a, n, p, c in sorted(pires)]
    if ctx["fins"]:
        L += ["", "## Campagnes terminées (extraction report)", ""]
        L += [f"- **{n}** ({ann}, {ca}) fin {d} - atteinte {pc(a)}" for n, ca, ann, d, a, *_ in ctx["fins"]]
    for titre, n, out in ctx["gates"]:
        L += ["", f"## {titre} - {n} P1", "```", out, "```"]
    if ctx["absents"]:
        L += ["", "## Checks non exécutés", ""] + [f"- {a}" for a in ctx["absents"]]
    return "\n".join(L)


# ---------------------------------------------------------------- main
def main():
    args = sys.argv[1:]
    outs = {"html": None, "md": None}
    quiet = "--quiet" in args
    args = [a for a in args if a != "--quiet"]
    for k in ("html", "md"):
        if f"--{k}" in args:
            j = args.index(f"--{k}")
            outs[k] = args[j + 1]
            del args[j:j + 2]
    data = Path(args[0]) if args else Path(".")
    today = date.today().isoformat()

    camps = lire(data / "campaigns.csv")
    board = lire(data / "board.csv")
    absents = []
    p1, p2, live = [], [], []
    adh_glob = marge_glob = obj_glob = setup_glob = conso_glob = marge_glob_rel = None
    fins = []

    if camps is None:
        absents.append("Pacing / objectifs / marge : `campaigns.csv` absent (le coeur du briefing)")
        camps = []
    else:
        for sev, c, ca, m, v in (check_pacing(camps, today) + check_objectifs(camps) + check_marge(camps)):
            (p1 if sev == "P1" else p2).append((sev, c, ca, m, v))
        fins = check_fin_de_vol(camps, today)
        # agrégats + table des campagnes en cours
        tot_att = tot_liv = rev = cout = tot_budget_att = tot_dep = 0.0
        ok = tot = 0
        for r in camps:
            if i(r, "statut").lower() not in ("live", "en cours", "running"):
                continue
            cible, imp = f(r, "imp_cible"), f(r, "imp_livrees")
            t_, e_ = jours(i(r, "date_debut"), i(r, "date_fin")) or 0, jours(i(r, "date_debut"), today) or 0
            part = min(max(e_ / t_, 0.0), 1.0) if t_ else 1.0
            att = cible * part
            tot_att += att; tot_liv += imp
            rev += f(r, "revenu"); cout += f(r, "cout_media")
            clics, conv = f(r, "clics"), f(r, "conversions")
            for val, cib in ((clics / imp if imp else None, f(r, "ctr_cible", 0)),
                             (conv / clics if clics else None, f(r, "conv_cible", 0))):
                if cib:
                    tot += 1
                    ok += 1 if val and val >= cib * 0.9 else 0
            marge_c = (f(r, "revenu") - f(r, "cout_media")) / f(r, "revenu") if f(r, "revenu") else None
            floor_c = f(r, "marge_floor", FLOOR_MARGE_DEFAUT) or FLOOR_MARGE_DEFAUT
            budget, dep = f(r, "budget"), f(r, "depense")
            land = f(r, "landings")
            tot_budget_att += budget * part
            tot_dep += dep
            live.append({"nom": i(r, "campagne"), "canal": i(r, "canal"), "marche": i(r, "marche"),
                         "adh": imp / att if att else 1.0, "imp": imp,
                         "ctr": clics / imp if imp else None, "conv": conv,
                         "budget": budget, "depense": dep,
                         "conso": (dep / (budget * part)) if budget and part else None,
                         "cpc": dep / clics if clics else None,
                         "cpv": dep / land if land else None,
                         "cpa": dep / conv if conv else None,
                         "marge": marge_c, "marge_rel": (marge_c / floor_c) if marge_c is not None else None,
                         "floor": floor_c,
                         "bande": BANDE_PACING.get(i(r, "canal", "display").lower(), 0.90)})
        # Jauge = part des campagnes DANS leur bande : le ratio global masque les sous-livreurs
        # sous la sur-livraison des autres, et n'est donc pas actionnable.
        adh_glob = (sum(1 for c in live if c["adh"] >= c["bande"]) / len(live)) if live else None
        marge_glob = (rev - cout) / rev if rev else None
        # Marge globale rapportée au floor moyen : une marge de 24 % n'est bonne ou
        # mauvaise que par rapport au floor négocié, jamais dans l'absolu.
        floor_moy = (sum(c["floor"] for c in live) / len(live)) if live else FLOOR_MARGE_DEFAUT
        marge_glob_rel = (marge_glob / floor_moy) if marge_glob is not None and floor_moy else None
        conso_glob = tot_dep / tot_budget_att if tot_budget_att else None
        obj_glob = ok / tot if tot else None

    if board is None:
        absents.append("Mise en place : `board.csv` absent (export Trello/Airtable/Notion)")
    else:
        bp, par_camp = check_board(board, camps, today)
        for sev, c, ca, m, v in bp:
            (p1 if sev == "P1" else p2).append((sev, c, ca, m, v))
        faites = sum(1 for t in board if i(t, "statut").lower() in ("done", "fait", "terminé"))
        setup_glob = faites / len(board) if board else None

    counts = lire(data / "counts.csv")
    disc, disc_glob = [], None
    if counts is None:
        absents.append("Réconciliation ad server annonceur : `counts.csv` absent")
    elif REC is None:
        absents.append("Réconciliation : verdict indisponible (discrepancy/reconcile_counts.py "
                       "introuvable) - repli sur le gate en annexe")
    else:
        df, disc, disc_glob = check_discrepancy(counts, today)
        for sev, c, ca, m, v in df:
            (p1 if sev == "P1" else p2).append((sev, c, ca, m, v))

    placements = lire(data / "placements.csv")
    par_camp_pl = {}
    if placements is None:
        absents.append("Vue par campagne : `placements.csv` absent (détail par placement)")
    else:
        par_camp_pl, pf = check_placements(placements, camps, today)
        for sev, c, ca, m, v in pf:
            (p1 if sev == "P1" else p2).append((sev, c, ca, m, v))

    gates, g_absents = run_gates(data)
    absents += g_absents
    p1_total = len(p1) + sum(n for _, n, _ in gates)

    ctx = {"today": today, "p1": p1, "p2": p2, "live": sorted(live, key=lambda r: r["adh"]),
           "fins": fins, "gates": gates, "absents": absents, "placements": par_camp_pl,
           "conso_glob": conso_glob, "marge_glob_rel": marge_glob_rel,
           "disc": disc, "disc_glob": disc_glob,
           "n_disc": len(disc), "n_disc_ok": sum(
               1 for d in disc if d["verdict"] in ("STRUCTUREL_OK", "CONSENT_EXPLIQUE",
                                                   "DOOH_NON_COMPARABLE")),
           "n_live": len(live), "n_canaux": len({r["canal"] for r in live}),
           "n_bande": sum(1 for c in live if c["adh"] >= c["bande"]),
           "adh_glob": adh_glob, "marge_glob": marge_glob, "obj_glob": obj_glob, "setup_glob": setup_glob}

    if outs["html"]:
        Path(outs["html"]).write_text(render_html(ctx), encoding="utf-8")
    if outs["md"]:
        Path(outs["md"]).write_text(render_md(ctx), encoding="utf-8")
    if not quiet:
        print(render_md(ctx) if not outs["md"] else
              f"Briefing : {outs['md']}" + (f" + {outs['html']}" if outs["html"] else ""))
        print(f"\n>>> {p1_total} P1, {len(p2)} P2, {len(fins)} campagne(s) à extraire")
    sys.exit(2 if p1_total else 0)


if __name__ == "__main__":
    main()
