#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""impression_log_probe.py — descente au niveau impression pour localiser un écart.

Usage: python3 impression_log_probe.py <log.csv> [--placement X]

Log attendu : un CSV impression-level avec des colonnes parmi (détection souple,
insensible à la casse) : id/imp_id, ts/timestamp/heure, placement, url/tag_url,
referrer/referer, ua/user_agent, consent/consentement, status/http_status.
Toute colonne absente = check correspondant sauté (signalé).

Localise à l'impression ce que l'agrégé ne montre pas :
  - IDs dupliqués (double comptage) ; trous de séquence horaire (fenêtre/fuseau)
  - referrers : % vide (SSAI/in-app/proxy), domaines inattendus, AMP/webviews,
    referrer http sur tag https
  - URLs par impression : taux de macros NON résolues en prod, cachebuster absent,
    présence des params clés (dclid/gclid/cb) par referrer
  - consentement : taux par domaine de referrer (où l'écart Google se fabrique)
  - user-agents : part de bots connus, part de webviews in-app
  - status HTTP : taux de non-200 par domaine

Stdlib, zéro réseau, exit 0 = rien de suspect, 2 = suspects trouvés.
"""
import csv
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import urlsplit, parse_qsl

MACRO_RX = re.compile(r"%%[A-Z_]+%%|\$\{[A-Z_:]+\}|\[[A-Za-z_]+\]|__[A-Z]+__")
BOT_RX = re.compile(r"bot|spider|crawl|monitor|preview|headless|python-requests|curl", re.I)
WEBVIEW_RX = re.compile(r"; wv\)|WebView|FBAV|FBAN|Instagram|GSA/", re.I)
AMP_HOSTS = ("cdn.ampproject.org", "amp.")


def col(fieldnames, *cands):
    low = {f.lower().strip(): f for f in fieldnames}
    for c in cands:
        if c in low:
            return low[c]
    return None


def dom(u):
    try:
        return urlsplit(u).netloc.lower() or "(vide)"
    except Exception:
        return "(illisible)"


def pct(n, d):
    return f"{100*n/max(d,1):.1f} %"


def main():
    args = sys.argv[1:]
    place_filter = None
    if "--placement" in args:
        i = args.index("--placement")
        place_filter = args[i + 1]
        del args[i:i + 2]
    if not args:
        print(__doc__)
        sys.exit(1)

    rows = list(csv.DictReader(open(args[0], newline="", encoding="utf-8")))
    if not rows:
        print("log vide")
        sys.exit(1)
    fn = rows[0].keys()
    c_id = col(fn, "id", "imp_id", "impression_id")
    c_ts = col(fn, "ts", "timestamp", "heure", "time")
    c_pl = col(fn, "placement", "placement_id", "pid")
    c_url = col(fn, "url", "tag_url", "tracking_url")
    c_ref = col(fn, "referrer", "referer", "page_url")
    c_ua = col(fn, "ua", "user_agent", "useragent")
    c_co = col(fn, "consent", "consentement", "consent_status")
    c_st = col(fn, "status", "http_status", "code")

    if place_filter and c_pl:
        rows = [r for r in rows if r[c_pl] == place_filter]
    n = len(rows)
    print(f"{n} impressions analysées" + (f" (placement {place_filter})" if place_filter else ""))
    findings = 0

    def flag(sev, msg):
        nonlocal findings
        if sev != "INFO":
            findings += 1
        print(f"  [{sev}] {msg}")

    # --- doublons ---
    if c_id:
        dup = [k for k, v in Counter(r[c_id] for r in rows if r[c_id]).items() if v > 1]
        if dup:
            flag("ERREUR", f"{len(dup)} ID(s) d'impression dupliqués (double comptage) - ex : {dup[:3]}")
        else:
            print("  [OK] pas de doublon d'ID")
    else:
        print("  [SKIP] pas de colonne id")

    # --- distribution horaire (fuseau/fenêtre) ---
    if c_ts:
        hrs = Counter()
        for r in rows:
            m = re.search(r"[T ](\d{2}):", r[c_ts] or "")
            if m:
                hrs[int(m.group(1))] += 1
        if hrs:
            h0 = hrs.get(0, 0) + hrs.get(23, 0)
            if h0 > n * 0.25:
                flag("ALERTE", f"{pct(h0, n)} des impressions à cheval sur minuit : suspicion de décalage de fuseau/fenêtre de comptage")

    # --- referrers ---
    if c_ref:
        doms = Counter(dom(r[c_ref] or "") for r in rows)
        vides = doms.get("(vide)", 0)
        if vides > n * 0.30:
            flag("ALERTE", f"referrer vide sur {pct(vides, n)} : SSAI/in-app/proxy - le tag annonceur y perd son contexte (comptage dégradé attendu)")
        amp = sum(v for k, v in doms.items() if any(a in k for a in AMP_HOSTS))
        if amp:
            flag("ALERTE", f"{pct(amp, n)} d'impressions AMP : environnement pauvre en signal pour les tags tiers")
        top = ", ".join(f"{k} ({pct(v, n)})" for k, v in doms.most_common(5))
        print(f"  [INFO] top referrers : {top}")
        if c_url:
            http_ref = sum(1 for r in rows if (r[c_ref] or "").startswith("http://")
                           and (r[c_url] or "").startswith("https://"))
            if http_ref:
                flag("ALERTE", f"referrer http vers tag https sur {pct(http_ref, n)} : referrer strippé -> attribution dégradée")

    # --- URLs par impression ---
    if c_url:
        macro = sum(1 for r in rows if MACRO_RX.search(r[c_url] or ""))
        if macro:
            flag("ERREUR", f"macros NON résolues en prod sur {pct(macro, n)} des impressions (cache/comptage cassés) - lint : check_tracking_url.py")
        no_cb = 0
        key_params = Counter()
        for r in rows:
            q = dict(parse_qsl(urlsplit(r[c_url] or "").query))
            kl = {k.lower() for k in q}
            if not kl & {"cb", "ord", "cachebuster", "rnd", "ts", "random"}:
                no_cb += 1
            for p in ("dclid", "gclid"):
                if p in kl:
                    key_params[p] += 1
        if no_cb > n * 0.05:
            flag("ALERTE", f"cachebuster absent sur {pct(no_cb, n)} des URLs : mise en cache -> sous-comptage côté annonceur")
        for p, v in key_params.items():
            print(f"  [INFO] {p} présent sur {pct(v, n)}")

    # --- consentement ---
    if c_co:
        ko = sum(1 for r in rows if (r[c_co] or "").strip().lower() in ("0", "false", "denied", "non", "no", ""))
        flag("INFO" if ko < n * 0.2 else "ALERTE",
             f"consentement refusé/absent : {pct(ko, n)} - autant d'impressions invisibles pour un ad server Google")
        if c_ref and ko:
            par_dom = defaultdict(lambda: [0, 0])
            for r in rows:
                d = dom(r[c_ref] or "")
                par_dom[d][1] += 1
                if (r[c_co] or "").strip().lower() in ("0", "false", "denied", "non", "no", ""):
                    par_dom[d][0] += 1
            worst = sorted(((k, ko_, tot) for k, (ko_, tot) in par_dom.items() if tot >= max(5, n * 0.02)),
                           key=lambda x: -x[1] / x[2])[:3]
            for k, ko_, tot in worst:
                print(f"  [INFO]   pire consentement : {k} -> {pct(ko_, tot)} refusé ({tot} imp.)")

    # --- user agents ---
    if c_ua:
        bots = sum(1 for r in rows if BOT_RX.search(r[c_ua] or ""))
        wv = sum(1 for r in rows if WEBVIEW_RX.search(r[c_ua] or ""))
        if bots > n * 0.05:
            flag("ALERTE", f"UA de type bot sur {pct(bots, n)} : IVT probable - un côté filtre, l'autre pas = écart")
        if wv:
            print(f"  [INFO] webviews in-app : {pct(wv, n)} (signal pauvre pour les tags tiers)")

    # --- status HTTP ---
    if c_st:
        ko = Counter(r[c_st] for r in rows if r[c_st] and not str(r[c_st]).startswith("2"))
        if sum(ko.values()) > n * 0.02:
            flag("ERREUR", f"réponses non-2xx sur {pct(sum(ko.values()), n)} : {dict(ko.most_common(3))}")

    print(f"\n{findings} finding(s)")
    sys.exit(2 if findings else 0)


if __name__ == "__main__":
    main()
