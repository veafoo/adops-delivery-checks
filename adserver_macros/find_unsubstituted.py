#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""find_unsubstituted.py — traque les macros NON SUBSTITUÉES en production.

Une macro qui part telle quelle dans un appel réel est un bug silencieux : le
serveur reçoit littéralement `%%CACHEBUSTER%%` au lieu d'un nombre, ou
`${CLICK_URL_UNESC}` au lieu d'une URL. Rien ne casse visiblement — l'appel
part, renvoie 200 — mais l'impression est mise en cache, le clic ne redirige
pas, ou la donnée arrive inexploitable. C'est une cause majeure d'écart de
comptage, et elle ne se voit qu'en regardant les appels réels.

Usage :
  python3 find_unsubstituted.py <fichier>                 # log, export, ou liste d'URLs
  python3 find_unsubstituted.py <fichier> --colonne url   # CSV : ne scanner qu'une colonne
  python3 find_unsubstituted.py --url "<une URL>"

Accepte : un texte quelconque (log serveur, HAR exporté, dump de tags), une
liste d'URLs, ou un CSV. Le format n'est pas imposé : on cherche des motifs.

Ce qui est détecté, à chaque étage de la chaîne :
  - adserving   : la macro est dans l'URL du tag appelé
  - tracking    : elle est dans un pixel ou un beacon (impression, quartile, clic)
  - redirection : elle est dans la cible d'un redirect (paramètre de type url=, r=, dest=)
  et le cas le plus vicieux : la macro ENCODÉE (%25%25CACHEBUSTER%25%25), qui
  prouve qu'elle a traversé un encodage sans jamais être substituée.

Stdlib, zéro réseau. Exit 0 = aucune macro résiduelle, 2 = au moins une.
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import macros as M  # noqa: E402

# Motifs génériques : ils attrapent aussi les macros propriétaires absentes de
# la table, ce qui est le but — on cherche « une macro non substituée », pas
# « une macro connue non substituée ».
MOTIFS = [
    (re.compile(r"%%[A-Za-z0-9_:]+%%"),        "style GAM / DCM (%%X%%)"),
    (re.compile(r"\$\{[A-Za-z0-9_]+\}"),       "style DV360 / OpenRTB (${X})"),
    # Le libellé disait « Amazon / Adform » : les deux ont été confrontés à la
    # doc éditeur et n'utilisent PAS cette famille (Adform est en __ADF*__,
    # Amazon en __CS_*__ / __AAX_*__). Le motif reste utile — plusieurs
    # plateformes propriétaires l'emploient — mais il ne nomme plus personne.
    (re.compile(r"\$[A-Z][A-Z0-9_]{2,}\$"),    "style propriétaire ($X$)"),
    (re.compile(r"%e[a-z]+!"),                 "style CM360 / GAM (%eX!)"),
    (re.compile(r"\[[A-Za-z][A-Za-z0-9_]{2,}\]"), "style Equativ / DOOH ([X])"),
    (re.compile(r"\{[a-z][a-z0-9_]{2,}\}"),    "style Google Ads ({x})"),
    (re.compile(r"__[A-Z][A-Z0-9_]+__"),       "style Adform / Amazon (__X__)"),
    (re.compile(r"\bTBD\b|\bXXXX+\b|INSERT_[A-Z_]+"), "reste de gabarit (TBD / XXXX / INSERT_)"),
]

# Formes encodées : la macro a traversé un urlencode sans être substituée.
MOTIFS_ENCODES = [
    (re.compile(r"%2525[A-Za-z0-9_]+%2525"), "macro %%X%% doublement encodée"),
    (re.compile(r"%25%25[A-Za-z0-9_]+%25%25"), "macro %%X%% encodée"),
    (re.compile(r"%24%7B[A-Za-z0-9_]+%7D"),  "macro ${X} encodée"),
    (re.compile(r"%5B[A-Za-z][A-Za-z0-9_]+%5D"), "macro [X] encodée"),
]

PARAMS_REDIR = {"url", "u", "r", "dest", "destination", "redirect", "redir", "rd", "adurl",
                "clkurl", "click", "clickurl", "landing", "lp", "target", "go", "out"}
INDICES_TRACKING = ("/imp", "/impression", "/track", "/pixel", "/beacon", "/event",
                    "/quartile", "/complete", "/click", "/clk", "/i.gif", ".gif", "/ping")


def _origine(macro: str) -> str:
    """À quelle plateforme cette syntaxe appartient-elle, si on la connaît.

    On interroge deux gisements, et l'ordre n'est pas neutre. D'abord les
    macros rattachées à un rôle, qui sont aussi celles que la traduction sait
    écrire. Ensuite les macros documentées HORS rôles : reconnaître une macro
    n'oblige pas à savoir la produire, et c'est précisément la distinction
    utile ici — un trafficker qui voit `__CS_APP_ID__` non substitué a besoin
    de savoir d'où elle sort, pas d'une invitation à la générer.
    """
    couples = M.toutes_les_syntaxes().get(macro)
    if couples:
        plats = sorted({M.LIBELLES[p] for p, _ in couples})
        roles = sorted({r for _, r in couples})
        return f" — {'/'.join(plats)}, rôle {'/'.join(roles)}"

    for plat, conf in M.PLATEFORMES.items():
        legende = (conf.get("_hors_roles") or {}).get(macro)
        if legende:
            return f" — {M.LIBELLES[plat]}, hors rôles : {legende}"
    return ""


def _decode_prudent(macro_encodee: str) -> str:
    """Décode une macro encodée sans la corrompre.

    `unquote` appliqué deux fois de suite sur `%25%25CACHEBUSTER%25%25` produit
    un octet invalide : le premier passage donne `%%CACHEBUSTER%%`, et le second
    interprète `%%C` comme une séquence d'échappement. On décode donc UNE fois,
    puis seulement si le résultat contient encore une séquence `%25`.
    """
    clair = unquote(macro_encodee)
    if "%25" in clair:
        clair = unquote(clair)
    return clair


def _etage(url: str, macro: str) -> str:
    """Où dans la chaîne la macro est-elle restée : adserving, tracking, redirection."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "indéterminé"
    # parse_qsl décode les valeurs : une macro ENCODÉE ne s'y retrouve pas telle
    # quelle. On teste donc la valeur décodée ET le segment brut de la query.
    for cle, valeur in parse_qsl(parts.query, keep_blank_values=True):
        if cle.lower() not in PARAMS_REDIR:
            continue
        brut = re.search(re.escape(cle) + r"=([^&]*)", parts.query)
        if macro in (valeur or "") or (brut and macro in brut.group(1)):
            return f"redirection (paramètre '{cle}')"
    chemin = (parts.path or "").lower()
    if any(ind in chemin for ind in INDICES_TRACKING):
        return "tracking (pixel ou beacon)"
    return "adserving (URL du tag)"


def scanner(texte: str, etiquette: str = "") -> list[tuple[str, str, str, str]]:
    """(sévérité, macro, étage, détail) pour chaque macro résiduelle trouvée."""
    out, vus = [], set()
    # Les URLs du texte, pour situer chaque macro dans la chaîne.
    urls = re.findall(r"https?://[^\s\"'<>)\]]+", texte)

    for rx, style in MOTIFS:
        for m in rx.findall(texte):
            if m in vus:
                continue
            vus.add(m)
            hote = next((u for u in urls if m in u), "")
            out.append(("ERREUR", m, _etage(hote, m) if hote else "indéterminé",
                        f"{style}{_origine(m)}"))

    for rx, style in MOTIFS_ENCODES:
        for m in rx.findall(texte):
            if m in vus:
                continue
            vus.add(m)
            clair = _decode_prudent(m)
            hote = next((u for u in urls if m in u), "")
            out.append(("ERREUR", m, _etage(hote, m) if hote else "indéterminé",
                        f"{style} (soit {clair}) : elle a traversé un encodage "
                        f"sans jamais être substituée{_origine(clair)}"))
    return out


def main() -> None:
    a = sys.argv[1:]
    if not a or "--help" in a:
        print(__doc__)
        sys.exit(0)

    colonne = a[a.index("--colonne") + 1] if "--colonne" in a else None
    if "--url" in a:
        blocs = [("--url", a[a.index("--url") + 1])]
    else:
        chemin = Path(a[0])
        if colonne and chemin.suffix.lower() == ".csv":
            with open(chemin, newline="", encoding="utf-8", errors="replace") as fh:
                lignes = [l for l in fh if l.strip() and not l.lstrip().startswith("#")]
            blocs = [(f"ligne {n+2}", (r.get(colonne) or ""))
                     for n, r in enumerate(csv.DictReader(lignes))]
        else:
            blocs = [(f"ligne {n+1}", l.rstrip("\n"))
                     for n, l in enumerate(open(chemin, encoding="utf-8", errors="replace"))
                     if l.strip() and not l.lstrip().startswith("#")]

    total, par_etage = 0, Counter()
    for etiquette, contenu in blocs:
        trouve = scanner(contenu, etiquette)
        if not trouve:
            continue
        print(f"\n{etiquette} : {contenu[:120]}{'...' if len(contenu) > 120 else ''}")
        for sev, macro, etage, detail in trouve:
            print(f"  [{sev}] {macro}  →  {etage}")
            print(f"          {detail}")
            par_etage[etage] += 1
            total += 1

    print()
    if total:
        print(f"{total} macro(s) NON SUBSTITUÉE(S) dans des appels réels :")
        for etage, n in par_etage.most_common():
            print(f"  {n:>3}  {etage}")
        print("\nChaque occurrence est un appel parti avec un littéral au lieu d'une valeur.")
    else:
        print("Aucune macro résiduelle : tous les appels scannés sont substitués.")
    sys.exit(2 if total else 0)


if __name__ == "__main__":
    main()
