#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diagnose_pacing.py — gate déterministe du runbook sous-livraison CTV (6 familles).

Usage: python3 diagnose_pacing.py <delivery_export.csv> [--json]

Entrée : un export de delivery par campagne x jour x source (CSV, colonnes documentées
dans demo/delivery_export.csv). Sortie : par campagne, la famille de cause suspecte
(1 supply / 2 contrôleur / 3 enchère / 4 mesure / 5 consentement / 6 accès supply),
la preuve chiffrée, et la prochaine vérification à faire.
Stdlib uniquement, zéro réseau.

La famille 6 distingue deux choses que la famille 1 confond : l'inventaire qui
n'existe pas, et l'inventaire qui existe mais qu'on n'a pas le droit d'acheter.
Sur du CTV premium, le second cas est le plus fréquent - l'accès passe par des
relations éditeurs directes, en PG ou en PMP. Monter l'enchère sur un inventaire
inaccessible ne produit rien : la famille 6 se teste donc AVANT la famille 3.
Colonne optionnelle `avails_adressables` (les avails réellement bidables compte
tenu des deals et de la liste d'inclusion) ; absente, la famille ne se déclenche pas.

Le script encode l'arbre de décision des six familles décrites ci-dessus. Verdict déterministe,
sans modèle ni réseau.
"""
import csv
import json
import sys
from collections import defaultdict

# Seuils du runbook (documentés, ajustables par plateforme)
ADHERENCE_ALERTE = 0.85     # livré/cible en-dessous -> campagne à risque
WIN_RATE_PLANCHER = 0.10    # sous ce win rate, l'enchère est suspecte
AVAILS_PLANCHER = 0.70      # avails réels / forecast sous ce ratio -> supply suspecte
ACCES_PLANCHER = 0.50       # avails adressables / avails réels sous ce ratio -> accès suspect
LATENCE_MESURE_H = 6        # latence de reporting au-delà -> mesure suspecte
CONSENT_CHUTE = 0.15        # chute relative du taux de consentement vs J-7 -> famille 5
OSCILLATION_SIGNE = 3       # nb de changements de signe de l'écart jour/jour -> contrôleur


def lire(chemin):
    campagnes = defaultdict(list)
    with open(chemin, newline="", encoding="utf-8") as f:
        # Ligne de commentaire en tete toleree (exports de demo du repo).
        propres = (l for l in f if l.strip() and not l.lstrip().startswith("#"))
        for ligne in csv.DictReader(propres):
            campagnes[ligne["campagne"]].append(ligne)
    for lignes in campagnes.values():
        lignes.sort(key=lambda r: r["jour"])
    return campagnes


def flt(ligne, cle, defaut=0.0):
    v = (ligne.get(cle) or "").strip()
    try:
        return float(v)
    except ValueError:
        return defaut


def forme_du_decrochage(lignes):
    """Régulier (contrôleur/enchère) ou en marches d'escalier (supply/contrainte)."""
    ecarts = [flt(r, "livre") - flt(r, "cible") for r in lignes]
    if len(ecarts) < 3:
        return "indéterminée", 0
    changements = sum(
        1 for a, b in zip(ecarts, ecarts[1:])
        if (a < 0 <= b) or (b < 0 <= a)
    )
    sauts = sum(
        1 for a, b in zip(ecarts, ecarts[1:])
        if abs(b - a) > 0.5 * max(1.0, abs(a))
    )
    if changements >= OSCILLATION_SIGNE:
        return "oscillation", changements
    if sauts >= 2:
        return "marches d'escalier", sauts
    return "régulière", changements


def diagnostiquer(nom, lignes):
    total_cible = sum(flt(r, "cible") for r in lignes)
    total_livre = sum(flt(r, "livre") for r in lignes)
    if total_cible <= 0:
        return None
    adherence = total_livre / total_cible
    forme, mesure_forme = forme_du_decrochage(lignes)

    # Piège classique : l'oscillation se moyenne. Une adhérence totale correcte
    # peut cacher un contrôleur qui alterne dumping et sous-livraison.
    if adherence >= ADHERENCE_ALERTE and forme != "oscillation":
        # Sept éléments comme les autres retours. Une forme de retour qui change selon
        # le verdict est un piège pour tout appelant qui indexe au-delà de d[2].
        return (nom, adherence, "OK", "adhérence dans la bande", "-", forme, [])

    derniere = lignes[-1]
    verdicts = []

    # Famille 5 — consentement (l'angle mort : à tester AVANT de toucher au bid)
    consent_j = flt(derniere, "taux_consentement", 1.0)
    consent_ref = flt(lignes[0], "taux_consentement", consent_j) or 1.0
    if consent_ref > 0 and (consent_ref - consent_j) / consent_ref >= CONSENT_CHUTE:
        verdicts.append(("5 CONSENTEMENT",
                         f"taux passé de {consent_ref:.0%} à {consent_j:.0%}",
                         "vérifier CMP/éditeur par marché ; traiter comme une panne de supply"))

    # Famille 1 — supply (avails vs forecast)
    avails = flt(derniere, "avails_reels", -1)
    forecast = flt(derniere, "avails_forecast", -1)
    if forecast > 0 and avails >= 0 and avails / forecast < AVAILS_PLANCHER:
        verdicts.append(("1 SUPPLY",
                         f"avails {avails:.0f} vs forecast {forecast:.0f} ({avails/forecast:.0%})",
                         "deal actif/plafonné ? daypart sans avails ? forecast à refaire"))

    # Famille 6 — accès supply (l'inventaire existe, on n'y a pas droit)
    # Avant la famille 3 : sur un inventaire inaccessible, monter le bid ne fait rien.
    adressables = flt(derniere, "avails_adressables", -1)
    if adressables >= 0 and avails > 0 and adressables / avails < ACCES_PLANCHER:
        verdicts.append(("6 ACCÈS SUPPLY",
                         f"avails adressables {adressables:.0f} sur {avails:.0f} réels "
                         f"({adressables/avails:.0%}) : l'inventaire existe, il n'est pas ouvert",
                         "deals directs PG/PMP à ouvrir, liste d'inclusion à élargir ; "
                         "ne PAS monter l'enchère, elle ne porte pas sur cet inventaire"))

    # Famille 4 — mesure (latence de reporting)
    latence = flt(derniere, "latence_reporting_h")
    if latence > LATENCE_MESURE_H:
        verdicts.append(("4 MESURE",
                         f"latence de reporting {latence:.0f} h",
                         "piloter sur fenêtre lissée ; le décrochage est peut-être déjà résorbé"))

    # Famille 2 — contrôleur (forme de courbe)
    if forme == "oscillation":
        verdicts.append(("2 CONTRÔLEUR",
                         f"{mesure_forme} inversions de signe de l'écart",
                         "lisser la mesure, zone morte, amortir le gain, plafonner l'accumulateur"))

    # Famille 3 — enchère (en dernier : jamais de bid avant d'avoir éliminé 1/4/5)
    win_rate = flt(derniere, "win_rate", -1)
    if 0 <= win_rate < WIN_RATE_PLANCHER:
        verdicts.append(("3 ENCHÈRE",
                         f"win rate {win_rate:.0%}",
                         "bid vs floor par paliers, net des fees ; APRÈS élimination de 1/4/5"))

    if not verdicts:
        verdicts.append(("À INVESTIGUER",
                         f"forme {forme}, aucune famille nette sur les colonnes fournies",
                         "compléter l'export (avails, avails adressables, win rate, "
                         "consentement) puis relancer"))

    famille, preuve, suite = verdicts[0]
    return (nom, adherence, famille, preuve, suite, forme,
            [f"{f} ({p})" for f, p, _ in verdicts[1:]])


def vers_dict(d):
    """Diagnostic sous forme structurée, pour un appelant qui n'est pas un humain.

    Un orchestrateur qui lirait l'affichage casserait au premier reformulage.
    """
    nom, adherence, famille, preuve, suite, forme, secondaires = d
    return {"campagne": nom, "adherence": round(adherence, 4), "famille": famille,
            "preuve": preuve, "prochaine_etape": suite, "forme": forme,
            "secondaires": secondaires}


def main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    json_out = "--json" in sys.argv[1:]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    campagnes = lire(args[0])

    if json_out:
        sortie = [vers_dict(d) for d in
                  (diagnostiquer(nom, lignes) for nom, lignes in campagnes.items())
                  if d is not None]
        print(json.dumps({"campagnes": sortie,
                          "a_risque": [c["campagne"] for c in sortie if c["famille"] != "OK"]},
                         ensure_ascii=False, indent=2))
        sys.exit(2 if any(c["famille"] != "OK" for c in sortie) else 0)

    a_risque = 0
    for nom, lignes in campagnes.items():
        d = diagnostiquer(nom, lignes)
        if d is None:
            continue
        if d[2] == "OK":
            print(f"[OK]      {d[0]:<24} adhérence {d[1]:.0%}")
            continue
        a_risque += 1
        nom_c, adh, famille, preuve, suite, forme, secondaires = d
        print(f"[RISQUE]  {nom_c:<24} adhérence {adh:.0%}  courbe: {forme}")
        print(f"          famille suspecte : {famille} — {preuve}")
        print(f"          prochaine étape  : {suite}")
        if secondaires:
            print(f"          aussi à vérifier : {', '.join(secondaires)}")
    print(f"\n{a_risque} campagne(s) à risque / {len(campagnes)}")
    sys.exit(2 if a_risque else 0)


if __name__ == "__main__":
    main()
