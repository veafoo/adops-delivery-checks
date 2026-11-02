#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_tracking_url.py — lint statique d'URLs de tracking/redirect publicitaires.

Usage:
  python3 check_tracking_url.py <fichier_urls.txt>     # une URL par ligne, lignes # ignorées
  python3 check_tracking_url.py --url "<URL>"          # une seule URL
  ... --follow                                          # OPT-IN réseau : suit la chaîne de redirects
  ... --decodages doubleclick.net=1 --decodages ssp.x=2 # combien de fois chaque maillon décode
  ... --decodages-defaut 1                              # hypothèse par défaut, assumée comme telle
  ... --template                                        # tag pas encore trafiqué : macros = normal
  python3 check_tracking_url.py --self-test

Détecte (hors ligne, zéro réseau par défaut) :
  - macros non résolues (GAM %%X%%, DV360/OpenRTB ${X}, CM360/générique [X], {X}, __X__)
  - problèmes d'encodage : espaces/quotes/pipes non encodés, URL imbriquée NON encodée
    -> "param bleed" (les params internes avalés par l'URL externe)
  - COHÉRENCE de la profondeur d'encodage avec la chaîne déclarée (--decodages) : le double
    encodage n'est PAS un défaut en soi, c'est l'écart avec le nombre de décodages en aval
    qui en est un, dans les deux sens (sous-encodé -> params perdus, sur-encodé -> 404)
  - redirects imbriqués : déroule récursivement url=/adurl=/dest=/r=/u=... et lint chaque niveau
  - paramètres : doublons, valeurs vides, cachebuster absent ou statique, casse UTM incohérente,
    typos fréquents, fragment mal placé, http:// sur un tracker (downgrade)
  - --follow : hops réels, codes HTTP, downgrade https->http, params perdus à chaque hop, boucles

Sortie : findings par URL avec sévérité (ERREUR/ALERTE/INFO). Exit 0 = propre, 2 = findings.
Verdict déterministe, sans modèle.
"""
import re
import sys
from urllib.parse import urlsplit, parse_qsl, unquote

REDIR_PARAMS = {"url", "u", "r", "dest", "destination", "redirect", "redir", "rd",
                "adurl", "clkurl", "click", "clickurl", "landing", "landingpage",
                "l", "target", "go", "next", "out", "ds_dest_url", "lp"}
CACHEBUSTER_PARAMS = {"cachebuster", "cb", "ord", "rnd", "random", "ts", "timestamp", "n", "c"}
MACRO_PATTERNS = [
    (re.compile(r"%%[A-Z_]+%%"), "macro GAM/DCM non résolue (%%X%%)"),
    (re.compile(r"\$\{[A-Z_:]+\}"), "macro DV360/OpenRTB non résolue (${X})"),
    (re.compile(r"\[[A-Za-z_]+\]"), "macro type CM360/Sizmek non résolue ([X])"),
    (re.compile(r"\{[a-z_]+\}"), "macro générique non résolue ({x})"),
    (re.compile(r"__[A-Z]+__"), "macro générique non résolue (__X__)"),
    (re.compile(r"%e[a-z]+!"), "macro CM360 placement non résolue (%eX!)"),
]
UTM_CANON = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}
TYPO_RX = re.compile(r"\b(ultm_|utm-s|camapign|campain|gclld|dcllid|source%3D)", re.I)
RAW_BAD = re.compile(r'[ "<>|\\^`{}]')
SCHEMA_CLAIR = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)

# ------------------------------------------------------------------------------------
# LA PROFONDEUR D'ENCODAGE N'EST PAS UN DEFAUT EN SOI
# ------------------------------------------------------------------------------------
# Une version precedente signalait tout double encodage (%252F, %253A) comme une alerte.
# C'est faux, et la preuve est dans les specs : OpenRTB, DV360 et GAM exposent tous une
# variante de macro `_ESC_ESC`. Si le double echappement etait un defaut, la norme ne le
# fournirait pas. Il est NECESSAIRE des qu'un maillon decode par defaut avant de passer
# la valeur au suivant.
#
# La regle juste est : **la profondeur d'encodage doit correspondre au nombre de
# decodages appliques en aval**, et l'ecart est un defaut DANS LES DEUX SENS.
#   - sous-encode : le maillon decode plus que ce qui a ete encode, les parametres de
#     l'URL interne sont avales (param bleed)
#   - sur-encode : la landing page recoit un `%3A` litteral, elle rend un 404
#
# Ce nombre de decodages n'est pas dans la donnee. C'est une propriete de la chaine, donc
# une declaration. Sans elle, le gate MESURE la profondeur et POSE la question, il ne
# tranche pas - meme patron que `--ordre` dans reconcile_counts.py.
DECODAGE_DEFAUT_IMPLICITE = 1   # ce qu'un redirecteur fait quand rien n'est declare


def profondeur_encodage(texte, maximum=6):
    """Combien de decodages avant que le schema apparaisse en clair. None si pas une URL.

    `https://x`          -> 0
    `https%3A%2F%2Fx`    -> 1
    `https%253A%252F%2Fx`-> 2
    """
    courant = texte
    for d in range(maximum + 1):
        if SCHEMA_CLAIR.match(courant):
            return d
        suivant = unquote(courant)
        if suivant == courant:
            return None
        courant = suivant
    return None


def parse_decodages(declarations):
    """`--decodages doubleclick.net=1 --decodages ssp.x=2` -> {suffixe: n}."""
    out = {}
    for d in declarations or []:
        hote, _, n = str(d).partition("=")
        hote = hote.strip().lower().lstrip(".")
        if not hote or not n.strip().isdigit():
            raise ValueError(f"declaration de decodage illisible : '{d}' "
                             f"(attendu hote=nombre)")
        out[hote] = int(n)
    return out


def decodages_de(netloc, table, defaut=None):
    """Nombre de decodages declares pour cet hote. Correspondance par suffixe.

    Le suffixe et pas l'egalite : un adserver expose `t1.eu.adsrv.com` et
    `t3.us.adsrv.com`, et personne ne declarera chaque sous-domaine a la main.
    """
    h = (netloc or "").lower().split("@")[-1].split(":")[0]
    candidats = [(len(s), n) for s, n in (table or {}).items()
                 if h == s or h.endswith("." + s)]
    if candidats:
        return max(candidats)[1], True
    return defaut, False


def findings_for(url, depth=0, out=None, decodages=None, defaut=None,
                 template=False):
    if out is None:
        out = []
    pad = "  " * depth
    tag = f"{pad}[niveau {depth}] "

    def add(sev, msg):
        out.append((sev, tag + msg))

    if depth > 4:
        add("ALERTE", "plus de 4 niveaux d'URL imbriquées : chaîne trop profonde")
        return out

    # Une macro non substituée est NORMALE dans un tag pas encore trafiqué, et n'est un
    # défaut que dans une URL capturée en production. Le gate ne peut pas distinguer les
    # deux contextes tout seul : par défaut il suppose la production, `--template` le dit.
    for rx, label in MACRO_PATTERNS:
        for m in set(rx.findall(url)):
            if template:
                add("INFO", f"{label} : {m} (attendu, l'URL est déclarée comme un modèle)")
            else:
                add("ERREUR", f"{label} : {m}")

    if RAW_BAD.search(url):
        add("ERREUR", f"caractères non encodés dans l'URL : {sorted(set(RAW_BAD.findall(url)))}")

    parts = urlsplit(url)
    if parts.scheme == "http":
        add("ERREUR", "tracker en http:// : downgrade (perte referrer/cookies, blocage mixed content)")
    if not parts.scheme:
        add("ERREUR", "URL sans schéma")
    if parts.fragment and ("=" in parts.fragment or "&" in parts.fragment):
        add("ALERTE", f"paramètres après le fragment (#) : perdus côté serveur -> '{parts.fragment[:60]}'")
    if len(url) > 2000:
        add("ALERTE", f"URL de {len(url)} caractères : risque de troncature (limites proxies/adservers)")

    params = parse_qsl(parts.query, keep_blank_values=True)
    seen = {}
    for k, v in params:
        seen.setdefault(k.lower(), []).append((k, v))

    for kl, lst in seen.items():
        if len(lst) > 1:
            add("ALERTE", f"paramètre en double : '{lst[0][0]}' x{len(lst)} (le comptage peut diverger selon lequel est lu)")
    for k, v in params:
        if v == "":
            add("ALERTE", f"paramètre vide : '{k}='")
        if TYPO_RX.search(k + "=" + v):
            add("ERREUR", f"typo probable dans '{k}={v[:40]}'")
    for k, _ in params:
        if k != k.lower() and k.lower() in UTM_CANON:
            add("ALERTE", f"casse UTM non canonique : '{k}' (GA est sensible à la casse)")

    cb = [v for k, v in params if k.lower() in CACHEBUSTER_PARAMS]
    if cb:
        for v in cb:
            if v and not any(rx.search(v) for rx, _ in MACRO_PATTERNS) and v.isdigit() and len(set(v)) <= 2:
                add("ALERTE", f"cachebuster statique suspect : '{v}' (mise en cache -> sous-comptage)")
    elif depth == 0 and any(re.search(r"(^|\.)" + re.escape(h) + r"(\.|$)", parts.netloc, re.I)
                            for h in ("doubleclick", "adsrv", "adserver", "serving")):
        # INFO et jamais plus. Le harnais a deja appris cette lecon sur la bibliotheque de
        # macros : exiger un cachebuster de TOUTE plateforme etait une contrainte fausse
        # (Broadsign n'en expose pas, Amazon DSP et Teads ne publient rien). Et la
        # correspondance se fait sur un LABEL de domaine, pas sur une sous-chaine : `ad.`
        # matchait `head.example.com`.
        add("INFO", "pas de cachebuster détecté sur un tracker d'impression : vérifier la "
                    "politique de cache. Toutes les plateformes n'en exposent pas, ce n'est "
                    "pas un défaut en soi")

    # --- URLs imbriquées : param bleed + lint récursif -------------------------
    # parse_qsl DÉCODE déjà les valeurs : pour savoir si l'URL imbriquée était
    # encodée dans l'original, il faut regarder la query BRUTE. Et il ne faut
    # surtout pas re-unquoter la valeur, sinon on corrompt les macros
    # (`%%CACHEBUSTER%%` deviendrait `%<octet invalide>CHEBUSTER%%`).
    attendu, declare = decodages_de(parts.netloc, decodages, defaut)
    for k, _v in params:
        if k.lower() not in REDIR_PARAMS:
            continue
        # On mesure la profondeur sur la valeur BRUTE de la query, pas sur celle rendue
        # par parse_qsl : parse_qsl decode deja une fois, donc une URL doublement encodee
        # n'y ressemble plus a une URL et le test `startswith("http")` la manquait
        # entierement. Defaut trouve au self-test, et il rendait le gate aveugle
        # precisement au cas qu'on venait d'ajouter.
        # Ancre sur un debut de parametre. Sans `(?:^|&)`, une query `?xu=zzz&u=<url>`
        # fait matcher `u=` A L'INTERIEUR de `xu=zzz` : le gate lit "zzz", n'y voit pas
        # une URL, et se tait sur un vrai defaut. Faux verdict en silence, trouve a la
        # relecture.
        m = re.search(r"(?:^|&)" + re.escape(k) + r"=([^&]*)", parts.query)
        brut = m.group(1) if m else ""
        mesuree = profondeur_encodage(brut)
        if mesuree is None:
            continue
        v = brut
        for _ in range(mesuree):
            v = unquote(v)

        if mesuree == 0 and "&" in parts.query.split(k + "=", 1)[-1]:
            add("ERREUR", f"URL imbriquée NON encodée dans '{k}=' : ses paramètres sont avalés "
                          f"par l'URL externe (param bleed) - tout ce qui suit le premier '&' "
                          f"interne est perdu au redirect")
        elif attendu is None:
            # Le gate MESURE et POSE la question. Il ne devine pas combien de fois le
            # maillon décode : ce n'est pas dans la donnée, c'est une propriété de la
            # chaîne, et l'inventer produirait un faux verdict dans un sens ou dans l'autre.
            if mesuree >= 2:
                add("INFO", f"'{k}=' est encodé {mesuree} fois. Ce n'est un défaut que si "
                            f"{parts.netloc} ne décode pas {mesuree} fois avant de rediriger. "
                            f"Déclarer la chaîne (--decodages {parts.netloc}=N) pour obtenir "
                            f"un verdict au lieu d'une mesure")
        elif mesuree < attendu:
            add("ERREUR", f"'{k}=' est encodé {mesuree} fois alors que {parts.netloc} décode "
                          f"{attendu} fois : au redirect, l'URL interne sera décodée plus "
                          f"qu'elle n'a été encodée et ses paramètres seront perdus")
        elif mesuree > attendu:
            add("ERREUR", f"'{k}=' est encodé {mesuree} fois alors que {parts.netloc} ne "
                          f"décode que {attendu} fois : la page d'atterrissage recevra une "
                          f"URL encore échappée (des '%3A' littéraux), donc un 404")
        elif not declare:
            add("INFO", f"'{k}=' encodé {mesuree} fois, cohérent avec le défaut de "
                        f"{attendu} décodage(s) supposé pour {parts.netloc}. Non déclaré : "
                        f"c'est une hypothèse, pas une vérification")

        findings_for(v, depth + 1, out, decodages, defaut, template)
    return out


# Hôtes que la chaîne de redirection ne doit jamais atteindre : une URL de
# tracking est une donnée non fiable, et --follow la suit réellement. Sans ce
# filtre, un redirect forgé pointant sur 169.254.169.254 (métadonnées cloud) ou
# sur un service interne transformerait l'outil en sonde SSRF.
_HOTES_INTERDITS = re.compile(
    r"^(localhost|127\.|0\.0\.0\.0|10\.|192\.168\.|169\.254\.|::1|\[::1\]|"
    r"172\.(1[6-9]|2[0-9]|3[01])\.|metadata\.google\.internal)", re.I)


def _hote_interdit(u):
    from urllib.parse import urlsplit
    h = (urlsplit(u).hostname or "").lower()
    return bool(_HOTES_INTERDITS.match(h))


def follow(url, max_hops=8):
    import urllib.request
    out, hops, cur, seen_urls = [], 0, url, set()
    if _hote_interdit(url):
        return [("ERREUR", "hôte local ou privé refusé : --follow ne sonde pas le réseau interne")]
    prev_params = dict(parse_qsl(urlsplit(url).query))
    while hops < max_hops:
        if cur in seen_urls:
            out.append(("ERREUR", f"boucle de redirection détectée au hop {hops}"))
            break
        seen_urls.add(cur)
        req = urllib.request.Request(cur, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (adops-delivery-checks)"})
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            out.append(("INFO", f"hop {hops}: {resp.status} {cur[:100]}"))
            break
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location")
            if e.code in (301, 302, 303, 307, 308) and loc:
                out.append(("INFO", f"hop {hops}: {e.code} -> {loc[:100]}"))
                if cur.startswith("https") and loc.startswith("http:"):
                    out.append(("ERREUR", f"downgrade https->http au hop {hops}"))
                if _hote_interdit(loc):
                    out.append(("ERREUR", f"redirection vers un hôte local ou privé refusée au hop {hops} : {loc[:80]}"))
                    break
                new_params = dict(parse_qsl(urlsplit(loc).query))
                lost = set(prev_params) - set(new_params)
                if lost:
                    out.append(("ALERTE", f"paramètres perdus au hop {hops}: {sorted(lost)}"))
                prev_params, cur, hops = new_params, loc, hops + 1
            else:
                out.append(("ERREUR", f"hop {hops}: HTTP {e.code} (chaîne cassée)"))
                break
        except Exception as ex:
            out.append(("ERREUR", f"hop {hops}: {type(ex).__name__}: {ex}"))
            break
    else:
        out.append(("ALERTE", f"chaîne non terminée après {max_hops} hops"))
    if hops > 3:
        out.append(("ALERTE", f"{hops} redirections : chaque hop coûte 1-3 % de comptage"))
    return out


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        sys.exit(self_test())
    do_follow = "--follow" in args
    template = "--template" in args
    args = [a for a in args if a not in ("--follow", "--template")]

    # --decodages hote=N, repetable. --decodages-defaut N pour supposer un decodage
    # partout ou rien n'est declare, en assumant que c'est une hypothese.
    declarations, defaut, restants = [], None, []
    i = 0
    while i < len(args):
        if args[i] == "--decodages" and i + 1 < len(args):
            declarations.append(args[i + 1]); i += 2
        elif args[i] == "--decodages-defaut" and i + 1 < len(args):
            defaut = args[i + 1]; i += 2
        else:
            restants.append(args[i]); i += 1
    args = restants
    try:
        table = parse_decodages(declarations)
        defaut = int(defaut) if defaut is not None else None
    except ValueError as e:
        print(f"{e}\n"); print(__doc__); sys.exit(1)

    if not args:
        print(__doc__)
        sys.exit(1)
    if args[0] == "--url":
        urls = [args[1]]
    else:
        urls = [l.strip() for l in open(args[0], encoding="utf-8")
                if l.strip() and not l.strip().startswith("#")]
    total = 0
    for u in urls:
        print(f"\n=== {u[:110]}{'...' if len(u) > 110 else ''}")
        f = findings_for(u, decodages=table, defaut=defaut, template=template)
        if do_follow:
            f += follow(u)
        if not f:
            print("  OK - aucun problème détecté")
        for sev, msg in f:
            print(f"  [{sev}] {msg}")
        total += sum(1 for s, _ in f if s in ("ERREUR", "ALERTE"))
    print(f"\n{total} finding(s) ERREUR/ALERTE sur {len(urls)} URL(s)")
    sys.exit(2 if total else 0)


def self_test():
    ok = fail = 0

    def check(nom, cond):
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print(f"  ECHEC : {nom}")

    def msgs(u, **kw):
        return [m for _, m in findings_for(u, **kw)]

    # Mesure de la profondeur
    check("URL en clair : profondeur 0", profondeur_encodage("https://x.com") == 0)
    check("URL echappee une fois", profondeur_encodage("https%3A%2F%2Fx.com") == 1)
    check("URL echappee deux fois", profondeur_encodage("https%253A%252F%252Fx.com") == 2)
    check("texte qui n'est pas une URL", profondeur_encodage("bonjour") is None)
    check("macro non confondue avec un encodage",
          profondeur_encodage("%%CACHEBUSTER%%") is None)

    # Declaration par suffixe
    t = parse_decodages(["doubleclick.net=1", "ssp.example=2"])
    check("suffixe reconnu", decodages_de("ad.doubleclick.net", t) == (1, True))
    check("hote exact reconnu", decodages_de("ssp.example", t) == (2, True))
    check("hote inconnu sans defaut", decodages_de("autre.com", t) == (None, False))
    check("hote inconnu avec defaut", decodages_de("autre.com", t, 1) == (1, False))
    check("le suffixe le plus long gagne",
          decodages_de("a.b.c", {"c": 1, "b.c": 3}) == (3, True))
    try:
        parse_decodages(["sans-nombre"])
        check("declaration illisible refusee", False)
    except ValueError:
        check("declaration illisible refusee", True)

    SIMPLE = "https://t.adsrv.com/c?url=https%3A%2F%2Fclient.com%2Flp%3Fa%3D1&cb=8347562891"
    DOUBLE = "https://t.adsrv.com/c?url=https%253A%252F%252Fclient.com%252Flp%253Fa%253D1&cb=8347562891"
    NU = "https://t.adsrv.com/c?url=https://client.com/lp?a=1&b=2&cb=8347562891"

    # LE faux positif corrige : sans declaration, un double encodage n'est plus une alerte.
    m = msgs(DOUBLE)
    check("le double encodage n'est plus une alerte en soi",
          not any("ALERTE" == s_ for s_, _ in findings_for(DOUBLE)))
    check("il est mesure et la question est posee",
          any("encode 2 fois" in x.replace("é", "e") for x in m))
    check("la question dit quoi declarer", any("--decodages" in x for x in m))

    # Avec la chaine declaree, le gate tranche - dans les deux sens.
    un = {"adsrv.com": 1}
    deux = {"adsrv.com": 2}
    check("un maillon qui decode 2 fois EXIGE le double encodage",
          not any(s_ == "ERREUR" for s_, _ in findings_for(DOUBLE, decodages=deux)))
    check("simple encodage sur un maillon qui decode 2 fois : sous-encode",
          any(s_ == "ERREUR" and "sera decodee plus" in m_.replace("é", "e")
              for s_, m_ in findings_for(SIMPLE, decodages=deux)))
    check("double encodage sur un maillon qui decode 1 fois : sur-encode",
          any(s_ == "ERREUR" and "404" in m_
              for s_, m_ in findings_for(DOUBLE, decodages=un)))
    check("simple encodage sur un maillon qui decode 1 fois : rien",
          not any(s_ == "ERREUR" for s_, m_ in findings_for(SIMPLE, decodages=un)))

    # Le param bleed reste detecte, il n'a pas ete remplace.
    check("param bleed toujours detecte", any("param bleed" in x for x in msgs(NU)))

    # Un parametre dont le nom se TERMINE par celui du parametre de redirection ne doit
    # pas capter sa valeur. Sans ancrage, `?xu=zzz&u=<url>` rendait le gate muet.
    PIEGE = ("https://t.adsrv.com/c?xu=zzz&u="
             "https%3A%2F%2Fclient.com%2Flp%3Fa%3D1&cb=8347562891")
    check("un parametre voisin ne masque pas la redirection",
          any(s_ == "ERREUR" and "'u='" in m_
              for s_, m_ in findings_for(PIEGE, decodages=deux)))

    # Le defaut suppose est signale comme une hypothese, pas comme une verification.
    check("le defaut suppose s'annonce comme une hypothese",
          any("hypothese" in x.replace("è", "e") for x in msgs(SIMPLE, defaut=1)))

    # Les niveaux imbriques heritent de la declaration : le maillon interne est juge
    # avec SA propre regle, pas avec celle du maillon externe.
    IMBRIQUE = ("https://t.adsrv.com/c?url=" +
                "https%3A%2F%2Fssp.example%2Fr%3Furl%3Dhttps%253A%252F%252Fclient.com")
    f = findings_for(IMBRIQUE, decodages={"adsrv.com": 1, "ssp.example": 2})
    check("le maillon externe est coherent",
          not any(s_ == "ERREUR" and "niveau 0" in m_ for s_, m_ in f))
    check("le maillon interne est juge sur sa propre declaration",
          any(s_ == "ERREUR" and "niveau 1" in m_ and "ssp.example" in m_
              for s_, m_ in f))

    # Une macro non resolue : erreur en production, normale dans un modele.
    TAG = "https://ad.adsrv.com/i?ord=%%CACHEBUSTER%%"
    check("macro non resolue en production = erreur",
          any(s_ == "ERREUR" for s_, _ in findings_for(TAG)))
    check("macro non resolue dans un modele = information",
          not any(s_ == "ERREUR" for s_, _ in findings_for(TAG, template=True)))

    # Le cachebuster absent ne devient jamais une erreur, et la correspondance d'hote se
    # fait sur un label de domaine.
    check("cachebuster absent reste une information",
          not any(s_ in ("ERREUR", "ALERTE")
                  for s_, _ in findings_for("https://ad.doubleclick.net/i?x=1")))
    check("un hote qui contient 'ad' par hasard n'est pas un adserver",
          not any("cachebuster" in m_
                  for _, m_ in findings_for("https://head.example.com/p?x=1")))

    print(f"\n{ok} verifications OK, {fail} echec(s)")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    main()
