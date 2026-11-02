#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translate.py — traduit un tag/redirect d'une plateforme vers une autre.

Usage :
  python3 translate.py --from cm360 --to dv360 --url "<tag>"
  python3 translate.py --to dv360 --url "<tag>"        # source devinée
  python3 translate.py --detect --url "<tag>"          # que reconnaît-on ?
  python3 translate.py --list                          # plateformes disponibles
  python3 translate.py --roles dv360                   # rôles exposés
  python3 translate.py --from cm360 --to dv360 --file tags.txt

Ce que l'outil fait vraiment : il conserve les RÔLES (identifiant de cache,
chaîne de clic, position...) et réécrit les SYNTAXES. Là où la cible n'expose
pas un rôle, il ne bricole pas : il le signale, parce qu'une macro fantôme
laissée dans un tag part en production telle quelle et casse le comptage.

Trois vérifications que personne ne fait à la main et qui cassent le trafficking :
  1. macro non traduisible -> signalée, jamais laissée en silence
  2. chaîne de clic insérée non encodée dans un paramètre -> avale la suite du tag
  3. tag ciblant l'Europe sans macro de consentement -> non-conformité

Zéro réseau, bibliothèque standard. Exit 0 = propre, 2 = au moins un avertissement.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import macros as M  # noqa: E402

# Une syntaxe de macro peut contenir des caractères regex : on échappe toujours.
def _rx(syntaxe: str) -> re.Pattern:
    return re.compile(re.escape(syntaxe), re.IGNORECASE)


def detecter(tag: str) -> list[tuple[str, int, list[str]]]:
    """Plateformes candidates, triées par nombre de macros reconnues.

    Masquage du plus long au plus court : les macros mono-caractère de CM360
    (`%c`, `%n`, `%u`, `%t`, `%r`) matchent sinon À L'INTÉRIEUR des macros
    longues des autres plateformes — `%c` se retrouve dans `%%CACHEBUSTER%%` —
    et l'outil déclarerait un tag GAM comme étant du CM360.
    """
    trouvees: dict[str, list[str]] = {}
    reste = tag
    for syntaxe, couples in sorted(M.toutes_les_syntaxes().items(), key=lambda kv: -len(kv[0])):
        rx = _rx(syntaxe)
        if not rx.search(reste):
            continue
        # Consommer la zone reconnue pour qu'une macro plus courte ne s'y glisse pas.
        reste = rx.sub(lambda m: "\x00" * len(m.group(0)), reste)
        for plat, role in couples:
            trouvees.setdefault(plat, []).append(f"{syntaxe} ({role})")
    return sorted(((p, len(v), v) for p, v in trouvees.items()), key=lambda x: -x[1])


def traduire(tag: str, source: str, cible: str) -> tuple[str, list[tuple[str, str]]]:
    """Réécrit les macros de `source` vers `cible`. Retourne (tag, avertissements)."""
    src, dst = M.roles(source), M.roles(cible)
    alertes: list[tuple[str, str]] = []
    out = tag
    dooh_cible = cible in M.PLATEFORMES_DOOH

    # Une plateforme dont la doc n'est pas publique n'expose aucun rôle. Sans
    # ce garde-fou la traduction rendrait le tag inchangé, sans rien dire :
    # l'utilisateur croirait le tag porté alors qu'il ne l'est pas.
    for sens, plat in (("source", source), ("cible", cible)):
        if M.STATUTS[plat][0] == "NON_PUBLIC":
            alertes.append(("ERREUR",
                            f"{M.LIBELLES[plat]} ({sens}) : aucune macro n'est publiée pour cette "
                            f"plateforme ({M.STATUTS[plat][1]}). Traduction impossible, le tag est "
                            f"rendu inchangé. Récupérer la table auprès du contact plateforme."))

    # Traiter les syntaxes les plus longues d'abord : `%%CLICK_URL_UNESC%%`
    # avant `%%CLICK_URL_ESC%%` n'a pas d'importance ici, mais une syntaxe
    # incluse dans une autre en aurait.
    for role, syntaxe in sorted(src.items(), key=lambda kv: -len(kv[1])):
        if not _rx(syntaxe).search(out):
            continue
        remplacement = dst.get(role)
        if remplacement:
            out = _rx(syntaxe).sub(remplacement.replace("\\", "\\\\"), out)
            continue
        # Pas d'équivalent : on ne devine pas.
        if dooh_cible and role in M.ROLES_ABSENTS_DOOH:
            alertes.append(("INFO", f"{syntaxe} ({role}) : ce rôle n'existe pas en DOOH "
                                    f"(un écran ne connaît personne) — macro à retirer du tag"))
        else:
            alertes.append(("ALERTE", f"{syntaxe} ({role}) : {M.LIBELLES[cible]} n'expose pas ce rôle. "
                                      f"Laissée telle quelle, elle partira NON substituée en production"))
    return out, alertes


def verifier(tag: str, cible: str) -> list[tuple[str, str]]:
    """Contrôles indépendants de la traduction, sur le tag final."""
    out: list[tuple[str, str]] = []
    dst_inv = M.index_inverse(cible)
    parts = urlsplit(tag)

    # 1. Chaîne de clic non encodée dans un paramètre : elle avale la suite du tag.
    for cle, valeur in parse_qsl(parts.query, keep_blank_values=True):
        role = dst_inv.get(valeur.strip())
        if role in ("click_url", "page_url", "referrer") and role == "click_url":
            variante = M.roles(cible).get("click_url_escaped")
            m = re.search(re.escape(cle) + r"=([^&]*)", parts.query)
            suffixe = parts.query.split(cle + "=", 1)[-1]
            if "&" in suffixe and variante:
                out.append(("ERREUR",
                            f"la chaîne de clic est passée NON encodée dans '{cle}=' et d'autres "
                            f"paramètres suivent : tout ce qui suit sera avalé au redirect. "
                            f"Utiliser la variante encodée {variante}, ou placer ce paramètre en dernier"))
            elif not variante:
                out.append(("ALERTE",
                            f"chaîne de clic dans '{cle}=' : vérifier que {M.LIBELLES[cible]} "
                            f"l'encode, sinon la placer en dernier paramètre"))

    # 2. Cachebuster : son absence fait mettre l'impression en cache = sous-comptage.
    cb = M.roles(cible).get("cachebuster")
    if cb and cb not in tag and not any(
            s in tag for s in ("cb=", "ord=", "rnd=", "random=", "cachebuster=")):
        out.append(("ALERTE", f"aucun cachebuster détecté : ajouter {cb} — sans lui l'impression "
                              f"est mise en cache et sous-comptée"))

    # 3. Consentement : requis dès qu'on diffuse en Europe.
    consent = {r: s for r, s in M.roles(cible).items() if r in M.ROLES_CONSENT}
    if consent and not any(s in tag for s in consent.values()):
        exemples = ", ".join(sorted(consent.values())[:3])
        out.append(("ALERTE", f"aucune macro de consentement : pour une diffusion en Europe, "
                              f"ajouter {exemples}"))

    # 4. Macro d'une AUTRE plateforme restée dans le tag (le classique du copier-coller).
    #    Même masquage longest-first que dans detecter(), pour la même raison.
    reste = tag
    for syntaxe, couples in sorted(M.toutes_les_syntaxes().items(), key=lambda kv: -len(kv[0])):
        rx = _rx(syntaxe)
        if not rx.search(reste):
            continue
        reste = rx.sub(lambda m: "\x00" * len(m.group(0)), reste)
        plats = {p for p, _ in couples}
        if cible not in plats:
            out.append(("ERREUR", f"{syntaxe} appartient à {', '.join(sorted(M.LIBELLES[p] for p in plats))} "
                                  f"et non à {M.LIBELLES[cible]} : elle ne sera jamais substituée"))
    return out


def _afficher(titre, lignes):
    print(f"\n{titre}")
    for sev, msg in lignes:
        print(f"  [{sev}] {msg}")


def main() -> None:
    a = sys.argv[1:]
    if not a or "--help" in a or "-h" in a:
        print(__doc__)
        sys.exit(0)

    if "--list" in a:
        print("Le statut dit d'OÙ VIENT la donnée, pas si elle est bonne.\n")
        for st in M.ORDRE_STATUTS:
            print(f"  {st:<12} {M.LEGENDE_STATUTS[st]}")
        print()
        for statut in M.ORDRE_STATUTS:
            print(f"--- {statut}")
            for k, lib in sorted(M.LIBELLES.items(), key=lambda kv: kv[1]):
                st, note = M.STATUTS[k]
                if st != statut:
                    continue
                print(f"  {k:<20} {lib:<30} {len(M.roles(k)):>2} rôles   {note}")
                src, le = M.SOURCES[k]
                if src:
                    print(f"  {'':<20} source ({le}) : {src}")
        sys.exit(0)

    if "--roles" in a:
        plat = a[a.index("--roles") + 1]
        for role, syn in sorted(M.roles(plat).items()):
            marque = " (URL, à encoder si imbriquée)" if role in M.ROLES_URL else ""
            print(f"  {role:<24} {syn}{marque}")
        sys.exit(0)

    def opt(nom):
        return a[a.index(nom) + 1] if nom in a else None

    tags = []
    if opt("--url"):
        tags = [opt("--url")]
    elif opt("--file"):
        tags = [l.strip() for l in open(opt("--file"), encoding="utf-8")
                if l.strip() and not l.startswith("#")]
    if not tags:
        print("Fournir --url ou --file.")
        sys.exit(1)

    total = 0
    for tag in tags:
        print(f"\n{'='*78}\nTAG : {tag[:150]}{'...' if len(tag) > 150 else ''}")
        candidats = detecter(tag)
        if "--detect" in a:
            if not candidats:
                print("  aucune macro connue reconnue")
            for plat, n, quoi in candidats[:4]:
                print(f"  {M.LIBELLES[plat]:<28} {n} macro(s) : {', '.join(quoi[:4])}")
            continue

        cible = opt("--to")
        if not cible:
            print("Fournir --to <plateforme> (ou --detect).")
            sys.exit(1)
        source = opt("--from")
        if not source:
            if not candidats:
                print("  source indéterminable : préciser --from")
                sys.exit(1)
            source = candidats[0][0]
            print(f"  source devinée : {M.LIBELLES[source]} ({candidats[0][1]} macro(s) reconnues)")

        traduit, alertes = traduire(tag, source, cible)
        controles = verifier(traduit, cible)
        print(f"\n  {M.LIBELLES[source]} -> {M.LIBELLES[cible]}\n")
        print(f"  {traduit}")
        if alertes:
            _afficher("  Rôles sans équivalent :", alertes)
        if controles:
            _afficher("  Contrôles du tag final :", controles)
        if not alertes and not controles:
            print("\n  Aucun avertissement : toutes les macros ont un équivalent et les contrôles passent.")
        total += sum(1 for s, _ in alertes + controles if s in ("ERREUR", "ALERTE"))

    if "--detect" not in a:
        print(f"\n{total} avertissement(s) au total")
    sys.exit(2 if total else 0)


if __name__ == "__main__":
    main()
