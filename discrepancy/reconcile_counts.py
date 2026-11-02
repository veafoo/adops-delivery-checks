#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reconcile_counts.py — moniteur de réconciliation sur une chaîne de points de comptage.

Usage: python3 reconcile_counts.py <counts.csv> [options]

CSV attendu (en-tête) :
  jour,campagne,placement,env,imp_plateforme,imp_adserver[,imp_ssp][,imp_verif][,taux_consentement]
  env ∈ web | inapp | ctv | dooh   (bande d'écart acceptée par environnement)

Deux lectures, et elles ne répondent pas à la même question.

1. LA CHAÎNE. Un même trafic est compté à plusieurs endroits : la plateforme au win,
   le SSP, l'ad server de l'annonceur, le tiers de vérification. Chaque maillon rend
   son ratio, et le gate signale quand la donnée CONTREDIT l'ordre déclaré - un point
   censé compter en aval qui compte plus qu'un point amont est soit une erreur de
   déclaration, soit une double comptabilisation.

2. LA LECTURE ÉCONOMIQUE. Le sens de l'écart change sa nature. Facturer plus qu'on
   n'achète expose au litige client ; acheter plus qu'on ne facture fait fuir la marge.
   Le gate ne devine JAMAIS quelle source facture et laquelle achète : sans --facturation
   et --achat, il pose la question au lieu d'y répondre. C'est une décision de
   gouvernance, pas une inférence de script.

Verdicts par placement (agrégé sur la période, avec analyse jour par jour) :
  TAG_MORT         adserver ≈ 0 alors que la plateforme sert -> tag jamais appelé
  RUPTURE          le ratio change de régime en cours de période
  BASCULE_ATTENDUE rupture datée d'un changement de point de comptage déclaré (--bascule)
  SORTIE_DE_BANDE  écart au-delà de la bande de l'environnement -> à investiguer (runbook)
  CONSENT_EXPLIQUE l'écart est expliqué par le taux de consentement (ad server Google)
  STRUCTUREL_OK    écart dans la bande, stable -> caractéristique du système, à documenter

Verdicts par maillon de chaîne :
  ALIGNE           les deux points comptent la même chose à la tolérance près
  ECART_MATERIEL   écart réel entre deux points successifs
  ORDRE_CONTREDIT  l'aval compte plus que l'amont : ordre déclaré faux, ou double comptage

Options :
  --ordre a,b,c          ordre déclaré des points de comptage, de l'amont vers l'aval
                         (défaut : imp_plateforme,imp_ssp,imp_adserver,imp_verif)
  --facturation <col>    la source qui facture le client
  --achat <col>          la source qui paie la supply
  --pilotage <col>       la source qui sert à piloter (informatif)
  --bascule <jour[=raison]>  un changement de point de comptage attendu à cette date.
                         Répétable. Une rupture datée de ce jour n'est plus une panne :
                         un changement de point de comptage crée un écart MÉCANIQUE avec
                         l'historique. Un gate qui crie au feu ce jour-là perd sa valeur.

Bandes par défaut (ratio adserver/plateforme accepté) : web >= 0.90, inapp >= 0.85, ctv >= 0.80.
dooh = non comparable (multiplicateur d'impressions) -> signalé, jamais réconcilié tel quel.
Stdlib uniquement, zéro réseau. Exit 0 = rien à investiguer, 2 = à investiguer.
"""
import csv
import json
import sys
from collections import defaultdict

BANDES = {"web": 0.90, "inapp": 0.85, "ctv": 0.80}
TOL_CONSENT = 0.05      # marge autour de l'écart expliqué par le consentement
SAUT_RUPTURE = 0.15     # variation de ratio jour/jour qui signe un changement de régime
TOL_ORDRE = 0.02        # au-delà, l'aval qui compte plus que l'amont n'est plus du bruit
ECART_MATERIEL = 0.03   # en-deçà, deux points de comptage sont considérés alignés

CHAINE_DEFAUT = ["imp_plateforme", "imp_ssp", "imp_adserver", "imp_verif"]

LIBELLES = {
    "imp_plateforme": "plateforme",
    "imp_ssp": "SSP",
    "imp_adserver": "ad server annonceur",
    "imp_verif": "tiers de vérification",
}


def libelle(col):
    return LIBELLES.get(col, col)


def flt(row, key, d=0.0):
    """Lit une valeur numérique d'une ligne CSV.

    Tolère la virgule décimale (exports français), les espaces de milliers et
    les cellules vides. Même signature que dans morning_check.py : les gates du
    repo partagent la convention (ligne, clé), pas (valeur).
    """
    try:
        return float(str(row.get(key, "")).replace(",", ".").replace(" ", "") or d)
    except (TypeError, ValueError, AttributeError):
        return d


def diagnostiquer(nom, lignes, env=None, bascules=None):
    """Rend le verdict d'un placement sur le couple plateforme <-> ad server.

    Retour : (nom, ratio, verdict, detail) — verdict parmi TAG_MORT, RUPTURE,
    BASCULE_ATTENDUE, SORTIE_DE_BANDE, CONSENT_EXPLIQUE, STRUCTUREL_OK,
    DOOH_NON_COMPARABLE, SANS_DONNEE.

    `bascules` : dict {jour: raison} des changements de point de comptage déclarés.
    """
    bascules = bascules or {}
    lignes = sorted(lignes, key=lambda r: r.get("jour", ""))
    env = (env or lignes[0].get("env", "web")).strip().lower()
    tp = sum(flt(r, "imp_plateforme") for r in lignes)
    ta = sum(flt(r, "imp_adserver") for r in lignes)
    if tp <= 0:
        return (nom, None, "SANS_DONNEE", "aucune impression plateforme sur la période")
    ratio = ta / tp
    ratios_j = [flt(r, "imp_adserver") / flt(r, "imp_plateforme")
                for r in lignes if flt(r, "imp_plateforme") > 0]
    consents = [flt(r, "taux_consentement", -1) for r in lignes]
    dispo = [c for c in consents if c >= 0]
    consent_moy = sum(dispo) / len(dispo) if dispo else None

    if env == "dooh":
        return (nom, ratio, "DOOH_NON_COMPARABLE",
                "multiplicateur d'impressions : ne jamais réconcilier tel quel, "
                "comparer les plays, pas les impressions")
    if ratio < 0.05:
        return (nom, ratio, "TAG_MORT",
                "l'ad server ne compte quasi rien : tag absent ou mauvais placement ID "
                "-> recette du placement")

    saut = max((abs(b - a) for a, b in zip(ratios_j, ratios_j[1:])), default=0.0)
    bande = BANDES.get(env, 0.90)
    if saut >= SAUT_RUPTURE:
        k = 1 + max(range(len(ratios_j) - 1), key=lambda x: abs(ratios_j[x + 1] - ratios_j[x]))
        jour = lignes[k].get("jour", "?")
        if jour in bascules:
            return (nom, ratio, "BASCULE_ATTENDUE",
                    f"changement de régime le {jour} (ratio {ratios_j[k-1]:.2f} -> "
                    f"{ratios_j[k]:.2f}) : bascule déclarée ({bascules[jour]}). L'écart avec "
                    f"l'historique est mécanique - comparer les périodes séparément, pas bout à bout")
        return (nom, ratio, "RUPTURE",
                f"changement de régime le {jour} "
                f"(ratio {ratios_j[k-1]:.2f} -> {ratios_j[k]:.2f}) : mode de comptage, "
                f"tag modifié, ou chute de consentement ce jour-là")
    if ratio >= bande:
        return (nom, ratio, "STRUCTUREL_OK",
                f"écart stable dans la bande ({env} >= {bande:.0%}) : à documenter, pas à négocier")
    if consent_moy is not None and ratio >= consent_moy - TOL_CONSENT:
        return (nom, ratio, "CONSENT_EXPLIQUE",
                f"ratio {ratio:.0%} ~ taux de consentement {consent_moy:.0%} : l'écart est le "
                f"consentement (ad server Google), pas une panne - à chiffrer au client")
    return (nom, ratio, "SORTIE_DE_BANDE",
            f"ratio {ratio:.0%} sous la bande {env} ({bande:.0%})"
            + (f" et sous le consentement ({consent_moy:.0%})" if consent_moy is not None else "")
            + " : runbook - fenêtre/fuseau, point de comptage, trafficking, blocage, dédup/IVT")


def totaux_par_source(lignes, ordre=None, exclure_dooh=False):
    """Total par point de comptage, en ne gardant que les colonnes réellement renseignées.

    `exclure_dooh` : le DOOH porte un multiplicateur d'impressions, le gate refuse
    déjà de le réconcilier placement par placement. L'agréger dans un total qui sert
    de base à une lecture financière ferait exactement l'erreur que le verdict
    DOOH_NON_COMPARABLE existe pour empêcher, mais en silence et sur le total.
    """
    ordre = ordre or CHAINE_DEFAUT
    if exclure_dooh:
        lignes = [r for r in lignes if (r.get("env") or "").strip().lower() != "dooh"]
    out = {}
    for col in ordre:
        if any((r.get(col) or "").strip() for r in lignes):
            out[col] = sum(flt(r, col) for r in lignes)
    return out


def analyser_chaine(lignes, ordre=None):
    """Ratio et verdict pour chaque maillon successif de la chaîne de comptage.

    Retour : liste de (amont, aval, total_amont, total_aval, ratio, verdict, detail).
    Les sources absentes du CSV sont ignorées : une chaîne à deux points reste valide.
    """
    ordre = ordre or CHAINE_DEFAUT
    totaux = totaux_par_source(lignes, ordre)
    presentes = [c for c in ordre if c in totaux and totaux[c] > 0]
    maillons = []
    for amont, aval in zip(presentes, presentes[1:]):
        ta, tv = totaux[amont], totaux[aval]
        ratio = tv / ta
        if ratio > 1 + TOL_ORDRE:
            verdict = "ORDRE_CONTREDIT"
            detail = (f"{libelle(aval)} compte {ratio - 1:.1%} de plus que {libelle(amont)}, "
                      f"qui est déclaré en amont : soit l'ordre déclaré est faux, soit un "
                      f"évènement est compté deux fois. À trancher avant toute lecture d'écart")
        elif ratio < 1 - ECART_MATERIEL:
            verdict = "ECART_MATERIEL"
            detail = (f"{libelle(aval)} compte {1 - ratio:.1%} de moins que {libelle(amont)}")
        else:
            verdict = "ALIGNE"
            detail = f"{libelle(amont)} et {libelle(aval)} comptent la même chose à {abs(1-ratio):.1%} près"
        maillons.append((amont, aval, ta, tv, ratio, verdict, detail))
    return maillons


def lecture_economique(totaux, source_facturation=None, source_achat=None):
    """Nature du risque porté par l'écart, selon le SENS et selon qui facture qui.

    Rend (verdict, detail). Sans déclaration de gouvernance, le gate REFUSE de nommer
    un risque financier : l'information manquante n'est pas dans la donnée, elle est
    dans une décision que quelqu'un doit prendre.
    """
    if not source_facturation or not source_achat:
        return ("GOUVERNANCE_NON_DECLAREE",
                "quelle source fait foi pour facturer le client, laquelle pour payer la "
                "supply ? Tant que ce n'est pas déclaré (--facturation, --achat), le sens "
                "de l'écart n'a pas de traduction financière")
    manquantes = [s for s in (source_facturation, source_achat) if s not in totaux or totaux[s] <= 0]
    if manquantes:
        return ("SOURCE_ABSENTE",
                f"déclarée mais absente du fichier ou à zéro : {', '.join(libelle(m) for m in manquantes)}")
    tf, tach = totaux[source_facturation], totaux[source_achat]
    ratio = tf / tach
    if ratio > 1 + ECART_MATERIEL:
        return ("RISQUE_SURFACTURATION",
                f"on facture sur {libelle(source_facturation)} ({tf:,.0f}) et on achète sur "
                f"{libelle(source_achat)} ({tach:,.0f}), soit {ratio - 1:.1%} facturés en plus "
                f"que constatés à l'achat : l'écart est favorable à la marge et défavorable au "
                f"client. Exposition au litige, pas fuite de marge")
    if ratio < 1 - ECART_MATERIEL:
        return ("FUITE_MARGE",
                f"on facture sur {libelle(source_facturation)} ({tf:,.0f}) et on achète sur "
                f"{libelle(source_achat)} ({tach:,.0f}), soit {1 - ratio:.1%} payés sans être "
                f"refacturés : c'est de la marge qui part, pas un litige client")
    return ("ALIGNE",
            f"facturation et achat comptent à {abs(1 - ratio):.1%} près : l'écart n'a pas "
            f"d'effet financier net")


A_INVESTIGUER = ("TAG_MORT", "RUPTURE", "SORTIE_DE_BANDE")
CHAINE_A_INVESTIGUER = ("ORDRE_CONTREDIT",)
ECO_A_INVESTIGUER = ("RISQUE_SURFACTURATION", "FUITE_MARGE")


def parse_args(argv):
    """Parse minimal, sans dépendance. Rend (chemin, options) ou lève ValueError."""
    chemin, opts = None, {"ordre": list(CHAINE_DEFAUT), "bascules": {},
                          "facturation": None, "achat": None, "pilotage": None,
                          "json": False}
    i = 0
    A_VALEUR = ("--ordre", "--facturation", "--achat", "--pilotage", "--bascule")
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            opts["json"] = True
            i += 1
            continue
        if a in A_VALEUR:
            if i + 1 >= len(argv):
                raise ValueError(f"{a} attend une valeur")
            i += 1
        if a == "--ordre":
            opts["ordre"] = [c.strip() for c in argv[i].split(",") if c.strip()]
        elif a in ("--facturation", "--achat", "--pilotage"):
            opts[a[2:]] = argv[i].strip()
        elif a == "--bascule":
            jour, _, raison = argv[i].partition("=")
            opts["bascules"][jour.strip()] = raison.strip() or "changement de point de comptage"
        elif a.startswith("--"):
            raise ValueError(f"option inconnue : {a}")
        elif chemin is None:
            chemin = a
        else:
            raise ValueError(f"argument en trop : {a}")
        i += 1
    if chemin is None:
        raise ValueError("chemin du CSV manquant")
    return chemin, opts


def main():
    try:
        chemin, opts = parse_args(sys.argv[1:])
    except ValueError as e:
        print(f"{e}\n")
        print(__doc__)
        sys.exit(1)

    with open(chemin, newline="", encoding="utf-8") as fh:
        # Ligne de commentaire en tete toleree (exports de demo du repo).
        rows = list(csv.DictReader(
            l for l in fh if l.strip() and not l.lstrip().startswith("#")))
    plc = defaultdict(list)
    for r in rows:
        plc[(r["campagne"], r["placement"], r["env"].strip().lower())].append(r)

    a_investiguer = 0
    structure = []
    for (camp, place, env), lignes in sorted(plc.items()):
        nom, ratio, verdict, detail = diagnostiquer(f"{camp} / {place}", lignes, env,
                                                    opts["bascules"])
        if verdict == "SANS_DONNEE":
            continue
        maillons = analyser_chaine(lignes, opts["ordre"])
        souci = (verdict in A_INVESTIGUER
                 or any(m[5] in CHAINE_A_INVESTIGUER for m in maillons))
        if souci:
            a_investiguer += 1
        structure.append({
            "campagne": camp, "placement": place, "env": env,
            "verdict": verdict, "detail": detail,
            "ratio": round(ratio, 4) if ratio is not None else None,
            "a_investiguer": souci,
            "imp_plateforme": sum(flt(r, "imp_plateforme") for r in lignes),
            "imp_adserver": sum(flt(r, "imp_adserver") for r in lignes),
            "chaine": [{"amont": m[0], "aval": m[1], "total_amont": m[2], "total_aval": m[3],
                        "ratio": round(m[4], 4), "verdict": m[5], "detail": m[6]}
                       for m in maillons],
        })
        if opts["json"]:
            continue
        flag = "  <-- INVESTIGUER" if souci else ""
        print(f"[{verdict:<18}] {nom} ({env})  plateforme="
              f"{sum(flt(r,'imp_plateforme') for r in lignes):,.0f} adserver="
              f"{sum(flt(r,'imp_adserver') for r in lignes):,.0f} ratio={ratio:.1%}{flag}")
        print(f"{'':21}{detail}")
        if len(maillons) > 1:
            for amont, aval, ta, tv, r_m, v_m, d_m in maillons:
                print(f"{'':21}  {libelle(amont)} -> {libelle(aval)} : {r_m:.1%}  [{v_m}]")
                if v_m in CHAINE_A_INVESTIGUER:
                    print(f"{'':21}    {d_m}")

    totaux_globaux = totaux_par_source(rows, opts["ordre"], exclure_dooh=True)
    eco = None
    if len(totaux_globaux) > 2 or opts["facturation"] or opts["achat"]:
        v_eco, d_eco = lecture_economique(totaux_globaux, opts["facturation"], opts["achat"])
        eco = {"verdict": v_eco, "detail": d_eco, "pilotage": opts["pilotage"]}
        if v_eco in ECO_A_INVESTIGUER:
            a_investiguer += 1
        if not opts["json"]:
            print(f"\n[{v_eco}] {d_eco}")
            if opts["pilotage"]:
                print(f"           pilotage déclaré sur {libelle(opts['pilotage'])}")

    if opts["json"]:
        print(json.dumps({"placements": structure, "lecture_economique": eco,
                          "totaux": totaux_globaux, "a_investiguer": a_investiguer,
                          "observes": len(plc)}, ensure_ascii=False, indent=2))
    else:
        print(f"\n{a_investiguer} placement(s) à investiguer / {len(plc)}")
    sys.exit(2 if a_investiguer else 0)


if __name__ == "__main__":
    main()
