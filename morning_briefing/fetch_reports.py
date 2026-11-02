#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_reports.py — récupère les exports quotidiens depuis une boîte mail (IMAP).

Le chaînon manquant de la routine : les statistiques de la veille arrivent par
mail, envoyées par la plateforme ou par un serveur de reporting. Ce script va
les chercher, dépose les pièces jointes dans le dossier de données attendu par
morning_check.py, et normalise leur nom.

    export BRIEFING_IMAP_HOST=imap.exemple.com
    export BRIEFING_IMAP_USER=reporting@exemple.com
    export BRIEFING_IMAP_PASS='mot-de-passe-application'
    python3 fetch_reports.py /data/delivery --depuis 1 --expediteur reporting@plateforme.com

Puis, dans le cron de 7h30, avant le briefing :
    fetch_reports.py /data/delivery --depuis 1 && send_briefing.sh /data/delivery "$LISTE"

CORRESPONDANCE NOM DE PIECE JOINTE -> FICHIER ATTENDU
Le script reconnaît par mots-clés (insensible à la casse) et renomme :
    delivery, pacing            -> campaigns.csv
    placement                   -> placements.csv
    discrepanc, reconcil, count -> counts.csv
    board, task, trello         -> board.csv
    url, tag, tracking          -> tracking_urls.txt
    ctv, ssai                   -> delivery_export.csv
Un fichier non reconnu est déposé sous son nom d'origine dans `brut/` et signalé :
mieux vaut un fichier ignoré visible qu'un fichier mal classé qui fausse un verdict.

Bibliothèque standard uniquement (imaplib, email). La connexion est en IMAP4_SSL,
les identifiants viennent de l'environnement et ne sont jamais écrits sur disque
ni journalisés. Lecture seule : les messages ne sont ni supprimés ni modifiés
(option --marquer-lu pour les marquer, désactivée par défaut).
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import sys
from datetime import date, timedelta
from email.header import decode_header, make_header
from pathlib import Path

CORRESPONDANCES = [
    (re.compile(r"discrepanc|reconcil|count", re.I), "counts.csv"),
    (re.compile(r"placement", re.I), "placements.csv"),
    (re.compile(r"board|task|trello|airtable", re.I), "board.csv"),
    (re.compile(r"ctv|ssai", re.I), "delivery_export.csv"),
    (re.compile(r"delivery|pacing|campaign|campagne", re.I), "campaigns.csv"),
    (re.compile(r"url|tag|tracking", re.I), "tracking_urls.txt"),
]


def cible(nom: str) -> str | None:
    for rx, dest in CORRESPONDANCES:
        if rx.search(nom):
            return dest
    return None


def decoder(valeur: str) -> str:
    try:
        return str(make_header(decode_header(valeur)))
    except Exception:
        return valeur or ""


def main() -> None:
    a = sys.argv[1:]
    if not a or "--help" in a:
        print(__doc__)
        sys.exit(0)

    dossier = Path(a[0])
    depuis = int(a[a.index("--depuis") + 1]) if "--depuis" in a else 1
    expediteur = a[a.index("--expediteur") + 1] if "--expediteur" in a else None
    marquer = "--marquer-lu" in a
    boite = a[a.index("--boite") + 1] if "--boite" in a else "INBOX"

    hote = os.environ.get("BRIEFING_IMAP_HOST")
    user = os.environ.get("BRIEFING_IMAP_USER")
    mdp = os.environ.get("BRIEFING_IMAP_PASS")
    if not all((hote, user, mdp)):
        print("Variables manquantes : BRIEFING_IMAP_HOST, BRIEFING_IMAP_USER, "
              "BRIEFING_IMAP_PASS.", file=sys.stderr)
        sys.exit(1)

    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "brut").mkdir(exist_ok=True)

    critere = ['SINCE', (date.today() - timedelta(days=depuis)).strftime("%d-%b-%Y")]
    if expediteur:
        critere += ['FROM', expediteur]

    ecrits, ignores = [], []
    with imaplib.IMAP4_SSL(hote) as M:
        M.login(user, mdp)
        M.select(boite, readonly=not marquer)
        typ, data = M.search(None, *critere)
        ids = data[0].split() if data and data[0] else []
        print(f"{len(ids)} message(s) depuis {critere[1]}"
              + (f" de {expediteur}" if expediteur else ""))
        for num in ids:
            typ, brut = M.fetch(num, "(RFC822)")
            if typ != "OK" or not brut or not brut[0]:
                continue
            msg = email.message_from_bytes(brut[0][1])
            sujet = decoder(msg.get("Subject", ""))
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                nom = decoder(part.get_filename() or "")
                if not nom:
                    continue
                contenu = part.get_payload(decode=True)
                if contenu is None:
                    continue
                # Le nom de la pièce jointe est une donnée non fiable : on ne
                # garde que le basename, jamais un chemin qui pourrait remonter.
                nom = os.path.basename(nom)
                dest = cible(nom) or cible(sujet)
                if dest:
                    (dossier / dest).write_bytes(contenu)
                    ecrits.append(f"{nom} -> {dest}")
                else:
                    (dossier / "brut" / nom).write_bytes(contenu)
                    ignores.append(nom)

    for e in ecrits:
        print(f"  [OK]      {e}")
    for i_ in ignores:
        print(f"  [IGNORÉ]  {i_} : aucune correspondance, déposé dans brut/")
    print(f"\n{len(ecrits)} fichier(s) placé(s), {len(ignores)} ignoré(s)")
    # Un dossier de données vide ferait tourner le briefing dans le vide.
    sys.exit(0 if ecrits else 3)


if __name__ == "__main__":
    main()
