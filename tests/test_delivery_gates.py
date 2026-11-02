#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests des gates delivery. Bibliothèque standard uniquement, aucun réseau.

    python3 -m unittest discover -s tests -v

Ce qui est testé ici est ce qui casse en vrai : la sémantique des couleurs (une
couleur fausse est pire que pas de couleur), l'arithmétique de pacing aux bornes,
la robustesse du lecteur CSV, et les cas où un agrégé rassurant cache un problème.
"""
import importlib.util
import json
import os
import re
import time
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]


def charger(chemin, nom):
    """Importe un script autonome comme module (les gates sont des exécutables)."""
    spec = importlib.util.spec_from_file_location(nom, RACINE / chemin)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nom] = mod
    spec.loader.exec_module(mod)
    return mod


mc = charger("morning_briefing/morning_check.py", "mc")
url = charger("discrepancy/check_tracking_url.py", "url_gate")
rec = charger("discrepancy/reconcile_counts.py", "rec_gate")


def j(n):
    return (date.today() + timedelta(days=n)).isoformat()


def campagne(**kw):
    base = {"campagne": "test", "annonceur": "A", "marche": "FR", "canal": "display",
            "statut": "live", "date_debut": j(-10), "date_fin": j(10),
            "budget": "10000", "depense": "5000", "imp_cible": "1000000",
            "imp_livrees": "500000", "clics": "2000", "landings": "1800",
            "conversions": "100", "revenu": "10000", "cout_media": "7500",
            "marge_floor": "0.20", "ctr_cible": "", "landing_cible": "",
            "conv_cible": "", "cpa_cible": ""}
    base.update({k: str(v) for k, v in kw.items()})
    return base


# --------------------------------------------------------------- sémantique couleur
class TestSemantiqueCouleur(unittest.TestCase):
    """La couleur dit 'où on en est vs le prévu', jamais 'beaucoup ou peu'."""

    def test_dans_la_bande_est_vert(self):
        self.assertEqual(mc.couleur(1.00, 0.90), mc.VERT)
        self.assertEqual(mc.couleur(0.90, 0.90), mc.VERT)

    def test_la_bande_depend_du_canal(self):
        # Le même 85 % est vert en CTV (bande 80) et orange en display (bande 90).
        # C'est LA règle du modèle : aucun seuil universel.
        self.assertEqual(mc.couleur(0.85, mc.BANDE_PACING["ctv"]), mc.VERT)
        self.assertEqual(mc.couleur(0.85, mc.BANDE_PACING["display"]), mc.ORANGE)

    def test_decrochage_rattrapable_est_orange(self):
        self.assertEqual(mc.couleur(0.80, 0.90), mc.ORANGE)   # 89 % de la bande

    def test_decrochage_severe_est_rouge(self):
        self.assertEqual(mc.couleur(0.70, 0.90), mc.ROUGE)    # 78 % de la bande

    def test_depassement_franc_n_est_pas_vert(self):
        # Sur-livrer ou sur-dépenser vide la fin de vol : ce n'est pas une réussite.
        self.assertEqual(mc.couleur(1.25, 0.90), mc.ORANGE)
        self.assertEqual(mc.couleur(1.50, 0.90), mc.ROUGE)

    def test_absence_de_donnee_est_grise_pas_rouge(self):
        # Ne pas savoir n'est pas un incident : confondre les deux crée de faux P1.
        self.assertEqual(mc.couleur(None), mc.GRIS)

    def test_la_barre_suit_la_meme_regle_que_la_jauge(self):
        self.assertIn(mc.VERT, mc.bar(0.85, mc.BANDE_PACING["ctv"]))
        self.assertIn(mc.ORANGE, mc.bar(0.85, mc.BANDE_PACING["display"]))


# --------------------------------------------------------------- pacing
class TestPacing(unittest.TestCase):
    def test_pile_dans_les_temps_ne_remonte_rien(self):
        # mi-flight, moitié livrée : rien à signaler
        self.assertEqual(mc.check_pacing([campagne(imp_livrees=500000)], date.today().isoformat()), [])

    def test_sous_livraison_severe_est_p1_avec_projection(self):
        r = mc.check_pacing([campagne(imp_livrees=200000)], date.today().isoformat())
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0][0], "P1")
        self.assertIn("projection", r[0][3])

    def test_sur_livraison_est_signalee(self):
        r = mc.check_pacing([campagne(imp_livrees=900000)], date.today().isoformat())
        self.assertTrue(any("sur-livraison" in x[3] for x in r))

    def test_tolerance_ctv_plus_large_que_display(self):
        # 82 % de la courbe : accepté en CTV (bande 80), signalé en display (bande 90)
        jour = date.today().isoformat()
        self.assertEqual(mc.check_pacing([campagne(canal="ctv", imp_livrees=410000)], jour), [])
        self.assertTrue(mc.check_pacing([campagne(canal="display", imp_livrees=410000)], jour))

    def test_campagne_non_live_est_ignoree(self):
        for st in ("setup", "fini"):
            self.assertEqual(mc.check_pacing([campagne(statut=st, imp_livrees=0)], date.today().isoformat()), [])

    def test_cible_ou_duree_nulle_ne_leve_pas_d_exception(self):
        # Une division par zéro dans un gate de 7h30 = pas de briefing du tout.
        jour = date.today().isoformat()
        mc.check_pacing([campagne(imp_cible=0)], jour)
        mc.check_pacing([campagne(date_debut=j(0), date_fin=j(0))], jour)


# --------------------------------------------------------------- objectifs et marge
class TestObjectifsEtMarge(unittest.TestCase):
    def test_ctr_tres_sous_la_cible_est_p1(self):
        r = mc.check_objectifs([campagne(clics=200, ctr_cible=0.0040)])  # CTR réel 0,04 %
        self.assertTrue(any(x[0] == "P1" and "CTR" in x[3] for x in r))

    def test_clics_qui_n_atteignent_pas_la_landing_pointe_le_redirect(self):
        # Le diagnostic doit accuser la chaîne de redirect, pas la créa.
        r = mc.check_objectifs([campagne(clics=2000, landings=400)])
        self.assertTrue(any("redirect" in x[3] for x in r))

    def test_marge_comparee_a_son_floor_pas_a_un_absolu(self):
        # 25 % de marge : bon avec un floor à 20, mauvais avec un floor à 40.
        self.assertEqual(mc.check_marge([campagne(revenu=10000, cout_media=7500, marge_floor=0.20)]), [])
        self.assertTrue(mc.check_marge([campagne(revenu=10000, cout_media=7500, marge_floor=0.40)]))

    def test_revenu_nul_ne_produit_pas_de_faux_positif(self):
        self.assertEqual(mc.check_marge([campagne(revenu=0)]), [])


# --------------------------------------------------------------- mise en place
class TestMiseEnPlace(unittest.TestCase):
    def tache(self, **kw):
        b = {"campagne": "test", "tache": "T", "statut": "todo", "echeance": j(5), "responsable": "R"}
        b.update(kw)
        return b

    def test_tache_bloquee_est_p1_immediat(self):
        f, _ = mc.check_board([self.tache(statut="blocked")], [campagne()], date.today().isoformat())
        self.assertTrue(any(x[0] == "P1" and "BLOQU" in x[3] for x in f))

    def test_go_live_imminent_avec_setup_incomplet_est_p1(self):
        camps = [campagne(campagne="c", statut="setup", date_debut=j(2))]
        board = [self.tache(campagne="c", statut="done"), self.tache(campagne="c", statut="todo")]
        f, _ = mc.check_board(board, camps, date.today().isoformat())
        self.assertTrue(any(x[0] == "P1" and "go-live" in x[3] for x in f))

    def test_setup_complet_ne_declenche_pas_d_alerte_go_live(self):
        camps = [campagne(campagne="c", statut="setup", date_debut=j(2))]
        board = [self.tache(campagne="c", statut="done"), self.tache(campagne="c", statut="fait")]
        f, _ = mc.check_board(board, camps, date.today().isoformat())
        self.assertFalse(any("go-live" in x[3] for x in f))


# --------------------------------------------------------------- placements
class TestVueParPlacement(unittest.TestCase):
    def test_placement_mort_detecte_alors_que_la_campagne_tient(self):
        """Le cas vicieux : agrégé à 99 %, un placement à 30 %."""
        camps = [campagne(campagne="c", imp_cible=1000000, imp_livrees=990000)]
        pls = [
            {"campagne": "c", "placement": "bon", "canal": "display", "budget": "5000",
             "depense": "2500", "imp_cible": "500000", "imp_livrees": "480000",
             "clics": "1000", "landings": "900", "conversions": "50",
             "revenu": "5000", "cout_media": "3800"},
            {"campagne": "c", "placement": "mort", "canal": "display", "budget": "5000",
             "depense": "300", "imp_cible": "500000", "imp_livrees": "75000",
             "clics": "10", "landings": "8", "conversions": "0",
             "revenu": "5000", "cout_media": "200"},
        ]
        _, f = mc.check_placements(pls, camps, date.today().isoformat())
        self.assertTrue(any(x[0] == "P1" and "mort" in x[3] for x in f),
                        "le placement hors bande doit remonter en P1")
        self.assertTrue(any("pas la campagne" in x[3] for x in f),
                        "le diagnostic doit désigner le placement, pas la campagne")


# --------------------------------------------------------------- lecteur CSV
class TestLecteurCSV(unittest.TestCase):
    def test_lignes_de_commentaire_ignorees(self):
        """Un préambule d'export faisait lire tout le fichier de travers, en silence."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_text("# export outil, 2026-07-26\n\na,b\n1,2\n", encoding="utf-8")
            rows = mc.lire(p)
            self.assertEqual(rows, [{"a": "1", "b": "2"}])

    def test_fichier_absent_rend_none_pas_une_exception(self):
        self.assertIsNone(mc.lire(Path("/inexistant/x.csv")))

    def test_nombre_a_virgule_decimale_accepte(self):
        # Les exports français écrivent 0,22 ; le gate ne doit pas lire 0.
        self.assertAlmostEqual(mc.f({"x": "0,22"}, "x"), 0.22)

    def test_valeur_illisible_ne_casse_pas_le_gate(self):
        self.assertEqual(mc.f({"x": "n/a"}, "x"), 0.0)


# --------------------------------------------------------------- lint d'URL
class TestLintURL(unittest.TestCase):
    def sev(self, u):
        return {s for s, _ in url.findings_for(u)}

    def test_macro_non_resolue_est_une_erreur(self):
        self.assertIn("ERREUR", self.sev("https://ad.x.com/i?ord=%%CACHEBUSTER%%"))
        self.assertIn("ERREUR", self.sev("https://ad.x.com/i?id=${AUCTION_ID}"))

    def test_param_bleed_detecte(self):
        """URL imbriquée non encodée : ses params sont avalés par l'URL externe."""
        f = url.findings_for("https://t.x.com/c?url=https://client.com/lp?a=1&b=2&cb=9")
        self.assertTrue(any("param bleed" in m for _, m in f))

    def test_url_imbriquee_correctement_encodee_ne_leve_pas_de_bleed(self):
        f = url.findings_for("https://t.x.com/c?url=https%3A%2F%2Fclient.com%2Flp%3Fa%3D1&cb=9")
        self.assertFalse(any("param bleed" in m for _, m in f))

    DOUBLE = ("https://t.adsrv.com/c?url="
              "https%253A%252F%252Fclient.com%252Flp%253Fa%253D1&cb=8347562891")
    SIMPLE = ("https://t.adsrv.com/c?url="
              "https%3A%2F%2Fclient.com%2Flp%3Fa%3D1&cb=8347562891")

    def test_le_double_encodage_seul_n_est_plus_un_defaut(self):
        """Faux positif corrigé le 22/08 : OpenRTB, DV360 et GAM exposent tous une macro
        `_ESC_ESC`. Si le double échappement était un défaut, la norme ne le fournirait
        pas. Sans déclaration de chaîne, le gate mesure et pose la question."""
        f = url.findings_for(self.DOUBLE)
        self.assertNotIn("ERREUR", {s for s, _ in f})
        self.assertNotIn("ALERTE", {s for s, _ in f})
        self.assertTrue(any("--decodages" in m for _, m in f),
                        "il doit dire quoi déclarer pour obtenir un verdict")

    def test_avec_la_chaine_declaree_le_gate_tranche_dans_les_deux_sens(self):
        """La règle juste : la profondeur d'encodage doit correspondre au nombre de
        décodages en aval, et l'écart est un défaut dans les deux sens."""
        deux = {"adsrv.com": 2}
        un = {"adsrv.com": 1}
        self.assertNotIn("ERREUR", {s for s, _ in
                                    url.findings_for(self.DOUBLE, decodages=deux)},
                         "un maillon qui décode 2 fois EXIGE le double encodage")
        self.assertIn("ERREUR", {s for s, _ in
                                 url.findings_for(self.SIMPLE, decodages=deux)},
                      "sous-encodé : les paramètres internes seront perdus")
        self.assertIn("ERREUR", {s for s, _ in
                                 url.findings_for(self.DOUBLE, decodages=un)},
                      "sur-encodé : la landing page recevra des %3A littéraux")
        self.assertNotIn("ERREUR", {s for s, _ in
                                    url.findings_for(self.SIMPLE, decodages=un)})

    def test_self_test_du_lint_url(self):
        r = subprocess.run(
            [sys.executable,
             str(RACINE / "discrepancy/check_tracking_url.py"),
             "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_http_sur_tracker_est_une_erreur(self):
        self.assertIn("ERREUR", self.sev("http://ad.x.com/i?cb=123456789"))

    def test_url_propre_ne_remonte_aucune_erreur(self):
        f = url.findings_for("https://ad.x.com/imp?cb=8347562891&utm_source=cm360&utm_medium=display")
        self.assertEqual([s for s, _ in f if s == "ERREUR"], [])

    def test_url_imbriquee_analysee_recursivement(self):
        f = url.findings_for("https://t.x.com/c?url=" +
                             "https%3A%2F%2Fclient.com%2Flp%3Ford%3D%25%25CACHEBUSTER%25%25")
        self.assertTrue(any("niveau 1" in m for _, m in f),
                        "les niveaux imbriqués doivent être lintés aussi")


# --------------------------------------------------------------- réconciliation
class TestReconciliation(unittest.TestCase):
    def lignes(self, paires, consent=None):
        out = []
        for n, (p, a) in enumerate(paires):
            r = {"jour": j(-len(paires) + n), "campagne": "c", "placement": "pl", "env": "web",
                 "imp_plateforme": str(p), "imp_adserver": str(a)}
            if consent is not None:
                r["taux_consentement"] = str(consent)
            out.append(r)
        return out

    def test_tag_mort(self):
        v = rec.diagnostiquer("c", self.lignes([(100000, 50), (100000, 40)]))
        self.assertEqual(v[2], "TAG_MORT")

    def test_ecart_stable_dans_la_bande_est_documente_pas_negocie(self):
        v = rec.diagnostiquer("c", self.lignes([(100000, 94000), (100000, 93500)]))
        self.assertEqual(v[2], "STRUCTUREL_OK")
        self.assertIn("documenter", v[3])

    def test_rupture_de_regime_datee(self):
        v = rec.diagnostiquer("c", self.lignes([(100000, 92000), (100000, 91000), (100000, 60000)]))
        self.assertEqual(v[2], "RUPTURE")

    def test_ecart_explique_par_le_consentement(self):
        # Ratio ~ 60 % et consentement ~ 60 % : ce n'est pas une panne.
        v = rec.diagnostiquer("c", self.lignes([(100000, 60000), (100000, 61000)], consent=0.60))
        self.assertEqual(v[2], "CONSENT_EXPLIQUE")

    def test_oscillation_non_masquee_par_une_moyenne_correcte(self):
        # Total à 95 % mais le ratio saute de 0,99 à 0,60 : c'est un évènement.
        v = rec.diagnostiquer("c", self.lignes([(100000, 99000), (100000, 60000), (100000, 99000)]))
        self.assertEqual(v[2], "RUPTURE")

    def test_dooh_jamais_reconcilie_comme_du_display(self):
        l = self.lignes([(400000, 1200)])
        l[0]["env"] = "dooh"
        v = rec.diagnostiquer("c", l)
        self.assertEqual(v[2], "DOOH_NON_COMPARABLE")


# --------------------------------------------------------------- bout en bout
class TestBoutEnBout(unittest.TestCase):
    """Les gates doivent tourner sur leurs fixtures et sortir le bon code."""

    def lancer(self, script, arg):
        return subprocess.run([sys.executable, str(RACINE / script), str(RACINE / arg)],
                              capture_output=True, text=True)

    def test_briefing_complet_sur_fixtures(self):
        r = self.lancer("morning_briefing/morning_check.py",
                        "morning_briefing/demo")
        self.assertEqual(r.returncode, 2, "les fixtures contiennent des P1 volontaires")
        for attendu in ("Briefing delivery", "Pacing dans la bande", "Placements hors bande"):
            self.assertIn(attendu, r.stdout)

    def test_probe_impression_sur_fixtures(self):
        r = self.lancer("discrepancy/impression_log_probe.py",
                        "discrepancy/demo/impressions.csv")
        self.assertEqual(r.returncode, 2)
        self.assertIn("dupliqu", r.stdout)          # doublons d'ID
        self.assertIn("consentement", r.stdout)     # consentement chiffré

    def test_diagnostic_pacing_ctv_sur_fixtures(self):
        r = self.lancer("ctv_pacing/diagnose_pacing.py",
                        "ctv_pacing/demo/delivery_export.csv")
        self.assertEqual(r.returncode, 2)
        for famille in ("SUPPLY", "CONTRÔLEUR", "ENCHÈRE", "MESURE", "CONSENTEMENT",
                        "ACCÈS SUPPLY"):
            self.assertIn(famille, r.stdout, f"la famille {famille} doit être démontrée")

    def test_reconciliation_sur_fixtures(self):
        r = self.lancer("discrepancy/reconcile_counts.py",
                        "discrepancy/demo/counts.csv")
        self.assertEqual(r.returncode, 2)
        # Les fixtures doivent démontrer la chaîne, pas seulement le couple.
        self.assertIn("ORDRE_CONTREDIT", r.stdout)
        self.assertIn("SSP", r.stdout)
        # Et sans déclaration, le gate pose la question de gouvernance.
        self.assertIn("GOUVERNANCE_NON_DECLAREE", r.stdout)

    def test_gouvernance_declaree_nomme_le_risque(self):
        r = subprocess.run(
            [sys.executable,
             str(RACINE / "discrepancy/reconcile_counts.py"),
             str(RACINE / "discrepancy/demo/counts.csv"),
             "--facturation", "imp_plateforme", "--achat", "imp_ssp"],
            capture_output=True, text=True)
        self.assertIn("RISQUE_SURFACTURATION", r.stdout)
        self.assertNotIn("GOUVERNANCE_NON_DECLAREE", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)


mac = charger("adserver_macros/macros.py", "macros")
trad = charger("adserver_macros/translate.py", "translate")


class TestBibliothequeMacros(unittest.TestCase):
    def test_toutes_les_plateformes_ont_un_libelle(self):
        for k, v in mac.PLATEFORMES.items():
            self.assertIn("_libelle", v, f"{k} sans libellé")

    def test_un_cachebuster_quand_la_plateforme_en_expose_un(self):
        # Ce test exigeait auparavant un cachebuster pour TOUTE plateforme.
        # C'était une contrainte fausse, et elle a coûté cher : elle rendait
        # plus simple d'inventer une syntaxe que de constater une absence.
        # Broadsign n'expose pas de cachebuster (famille player), Amazon DSP
        # et Teads ne publient rien. L'invariant utile n'est pas « toutes en
        # ont un » mais « celles qui servent du display en ont un ».
        for k in ("gam", "cm360", "dv360", "ttd", "xandr",
                  "equativ_adserver", "equativ_activation", "adform"):
            self.assertIn("cachebuster", mac.roles(k), f"{k} sans cachebuster")

    def test_une_plateforme_non_publique_n_expose_aucune_syntaxe(self):
        # Le contrat central de la bibliothèque : quand la doc n'est pas
        # accessible, on déclare le trou. Une syntaxe inventée dans un outil
        # de traduction produit un tag qui part en prod non substitué.
        for k, v in mac.PLATEFORMES.items():
            if v.get("_statut") == "NON_PUBLIC":
                self.assertEqual({}, mac.roles(k),
                                 f"{k} est declaree NON_PUBLIC mais expose des syntaxes")

    def test_une_plateforme_verifiee_cite_sa_source(self):
        for k, v in mac.PLATEFORMES.items():
            if v.get("_statut") == "VERIFIE_DOC":
                src, le = mac.SOURCES[k]
                self.assertTrue(src and src.startswith("http"),
                                f"{k} est VERIFIE_DOC sans URL de source")
                self.assertTrue(le, f"{k} est VERIFIE_DOC sans date de verification")

    def test_les_macros_hors_roles_ne_fuient_pas_dans_les_roles(self):
        # `_hors_roles` documente des macros réelles dont la valeur ne
        # correspond à aucun rôle (un nom de campagne n'est pas un ID). Elles
        # doivent rester consultables sans jamais devenir traduisibles :
        # sinon une traduction substituerait un nom là où un ID est attendu.
        for k, v in mac.PLATEFORMES.items():
            hors = v.get("_hors_roles")
            if not hors:
                continue
            self.assertIsInstance(hors, dict, f"{k} : _hors_roles doit etre un dict")
            fuites = set(hors) & set(mac.roles(k).values())
            self.assertEqual(set(), fuites,
                             f"{k} expose comme role des macros declarees hors roles : {fuites}")

    def test_amazon_separe_la_surface_publique_de_la_surface_fermee(self):
        # Amazon a deux surfaces distinctes et il ne faut pas les confondre :
        # les URL de mesure d'app sont documentées publiquement, les macros
        # créa du DSP ne le sont pas. Les fusionner ferait passer la seconde
        # pour vérifiée.
        self.assertEqual("NON_PUBLIC", mac.PLATEFORMES["amazon_dsp"]["_statut"])
        self.assertEqual({}, mac.roles("amazon_dsp"))
        self.assertEqual("VERIFIE_DOC", mac.PLATEFORMES["amazon_mmp"]["_statut"])
        self.assertIn("__CS_CREATIVE_ID__", mac.roles("amazon_mmp").values())
        # La surface de mesure n'a pas de cachebuster documenté : on ne lui
        # en invente pas un pour faire joli.
        self.assertNotIn("cachebuster", mac.roles("amazon_mmp"))

    def test_une_macro_hors_roles_est_reconnue_mais_pas_traduisible(self):
        # Les deux moitiés du contrat, dans le même test pour qu'on ne puisse
        # pas en satisfaire une en cassant l'autre.
        fu = charger("adserver_macros/find_unsubstituted.py", "findunsub")
        self.assertIn("Amazon", fu._origine("__CS_APP_ID__"))       # reconnue
        self.assertNotIn("__CS_APP_ID__",
                         mac.toutes_les_syntaxes())                  # pas traduisible

    def test_tout_statut_est_connu(self):
        for k, v in mac.PLATEFORMES.items():
            self.assertIn(v.get("_statut"), mac.ORDRE_STATUTS, f"{k} porte un statut inconnu")

    def test_traduire_vers_une_plateforme_non_publique_ne_se_tait_pas(self):
        # Sans garde-fou, la traduction rendrait le tag inchangé en silence.
        tag = "https://a.x/i?cb=${CACHEBUSTER}"
        out, alertes = trad.traduire(tag, "dv360", "amazon_dsp")
        self.assertEqual(tag, out, "rien ne doit etre traduit")
        self.assertTrue(any(sev == "ERREUR" for sev, _ in alertes),
                        "l'impossibilite de traduire doit etre une ERREUR, pas un silence")

    def test_les_roles_none_sont_ecartes(self):
        # `None` = rôle non exposé : il ne doit jamais apparaître comme syntaxe.
        self.assertNotIn("deal_id", mac.roles("cm360"))
        self.assertIn("deal_id", mac.roles("dv360"))

    def test_le_dooh_n_expose_pas_de_role_utilisateur(self):
        for dooh in ("vistar", "hivestack", "broadsign"):
            r = mac.roles(dooh)
            for absent in ("click_url", "ifa", "gdpr_consent"):
                self.assertNotIn(absent, r, f"{dooh} ne devrait pas exposer {absent}")
            self.assertIn("venue_id", r, f"{dooh} doit exposer venue_id")


class TestTraductionDeTags(unittest.TestCase):
    def test_traduction_conserve_les_roles(self):
        tag = "https://a.x/i?cb=%n&clk=%c&cid=%ecid!"
        out, _ = trad.traduire(tag, "cm360", "dv360")
        self.assertIn("${CACHEBUSTER}", out)
        self.assertIn("${CLICK_URL_UNESC}", out)
        self.assertIn("${CREATIVE_ID}", out)
        self.assertNotIn("%n", out)

    def test_role_sans_equivalent_est_signale_pas_invente(self):
        tag = "https://a.x/i?deal=${DEAL_ID}&cb=${CACHEBUSTER}"
        out, alertes = trad.traduire(tag, "dv360", "cm360")
        self.assertIn("${DEAL_ID}", out, "la macro non traduisible reste visible")
        self.assertTrue(any("DEAL_ID" in m for _, m in alertes),
                        "et elle doit être signalée, jamais laissée en silence")

    def test_porter_un_tag_mobile_en_dooh_signale_les_roles_absents(self):
        tag = "https://a.x/i?ifa=${ADVERTISING_IDENTIFIER}&clk=${CLICK_URL_UNESC}"
        _, alertes = trad.traduire(tag, "dv360", "vistar")
        msgs = " ".join(m for _, m in alertes)
        self.assertIn("DOOH", msgs)

    def test_detection_ne_confond_pas_macro_courte_et_macro_longue(self):
        """`%c` (CM360) est contenu dans `%%CACHEBUSTER%%` (GAM), en insensible à la casse."""
        candidats = dict((p, n) for p, n, _ in trad.detecter("https://a.x/i?cb=%%CACHEBUSTER%%"))
        self.assertIn("gam", candidats)
        self.assertNotIn("cm360", candidats,
                         "le masquage du plus long au plus court doit éviter le faux positif")

    def test_detection_d_un_tag_cm360_authentique(self):
        candidats = [p for p, _, _ in trad.detecter("https://a.x/i?cb=%n&clk=%c&page=%u")]
        self.assertEqual(candidats[0], "cm360")

    def test_clic_non_encode_suivi_d_autres_params_est_une_erreur(self):
        # La panne de trafficking la plus fréquente : tout ce qui suit est avalé.
        tag = "https://a.x/i?clk=${CLICK_URL_UNESC}&cb=${CACHEBUSTER}"
        sev = [s for s, _ in trad.verifier(tag, "dv360")]
        self.assertIn("ERREUR", sev)

    def test_clic_encode_ou_en_dernier_ne_leve_pas_d_erreur(self):
        tag = "https://a.x/i?cb=${CACHEBUSTER}&gdpr=${GDPR}&clk=${CLICK_URL_UNESC}"
        msgs = " ".join(m for s, m in trad.verifier(tag, "dv360") if s == "ERREUR")
        self.assertNotIn("avalé", msgs)

    def test_absence_de_cachebuster_signalee(self):
        msgs = " ".join(m for _, m in trad.verifier("https://a.x/i?cid=${CREATIVE_ID}", "dv360"))
        self.assertIn("cachebuster", msgs)

    def test_absence_de_consentement_signalee(self):
        msgs = " ".join(m for _, m in trad.verifier("https://a.x/i?cb=${CACHEBUSTER}", "dv360"))
        self.assertIn("consentement", msgs)

    def test_macro_etrangere_restee_dans_le_tag_est_nommee(self):
        tag = "https://a.x/i?cb=${CACHEBUSTER}&x=%%CLICK_URL_UNESC%%"
        msgs = " ".join(m for s, m in trad.verifier(tag, "dv360") if s == "ERREUR")
        self.assertIn("Google Ad Manager", msgs)


class TestDiscrepancyPremiereClasse(unittest.TestCase):
    """La réconciliation doit produire des P1/P2, pas seulement une annexe."""

    def lignes(self, paires, env="web", consent=None):
        out = []
        for n, (p, ad) in enumerate(paires):
            r = {"jour": j(-len(paires) + n), "campagne": "c", "placement": "pl", "env": env,
                 "imp_plateforme": str(p), "imp_adserver": str(ad)}
            if consent is not None:
                r["taux_consentement"] = str(consent)
            out.append(r)
        return out

    def test_le_verdict_est_reutilise_pas_reecrit(self):
        # Dupliquer les seuils dans deux gates garantit qu'ils divergeront.
        self.assertIsNotNone(mc.REC, "le verdict de réconciliation doit être importé")
        self.assertTrue(hasattr(mc.REC, "diagnostiquer"))

    def test_tag_mort_remonte_en_p1(self):
        f, detail, part = mc.check_discrepancy(self.lignes([(100000, 40), (100000, 30)]),
                                               date.today().isoformat())
        self.assertTrue(any(s == "P1" and "TAG MORT" in m for s, _, _, m, _ in f))
        self.assertEqual(part, 0.0, "un tag mort ne compte pas comme conforme")

    def test_ecart_structurel_ne_pollue_pas_les_p1(self):
        f, detail, part = mc.check_discrepancy(self.lignes([(100000, 94000), (100000, 93000)]),
                                               date.today().isoformat())
        self.assertEqual(f, [], "un écart stable dans la bande n'est pas une alerte")
        self.assertEqual(part, 1.0)

    def test_consentement_compte_comme_conforme(self):
        # L'écart EST le consentement : ce n'est pas une panne à corriger.
        _, _, part = mc.check_discrepancy(
            self.lignes([(100000, 60000), (100000, 61000)], consent=0.60),
            date.today().isoformat())
        self.assertEqual(part, 1.0)

    def test_rupture_remonte_en_p1_avec_sa_date(self):
        f, _, _ = mc.check_discrepancy(
            self.lignes([(100000, 92000), (100000, 91000), (100000, 55000)]),
            date.today().isoformat())
        self.assertTrue(any(s == "P1" and "RUPTURE" in m for s, _, _, m, _ in f))

    def test_briefing_complet_expose_la_jauge_de_comptage(self):
        r = subprocess.run(
            [sys.executable, str(RACINE / "morning_briefing/morning_check.py"),
             str(RACINE / "morning_briefing/demo")],
            capture_output=True, text=True)
        self.assertIn("Comptage conforme", r.stdout)


class TestRecuperationIMAP(unittest.TestCase):
    """Le nom d'une pièce jointe est une donnée non fiable."""

    def setUp(self):
        self.fetch = charger("morning_briefing/fetch_reports.py", "fetch")

    def test_correspondance_par_mot_cle(self):
        self.assertEqual(self.fetch.cible("export_discrepancies_2026-07-26.csv"), "counts.csv")
        self.assertEqual(self.fetch.cible("daily_placement_report.csv"), "placements.csv")
        self.assertEqual(self.fetch.cible("trello_board.csv"), "board.csv")
        self.assertEqual(self.fetch.cible("pacing_delivery.csv"), "campaigns.csv")

    def test_discrepancy_prime_sur_delivery_dans_un_nom_ambigu(self):
        # « daily delivery discrepancies » doit aller dans counts.csv, pas campaigns.csv :
        # l'ordre des correspondances est significatif et doit rester stable.
        self.assertEqual(self.fetch.cible("daily_delivery_discrepancies.csv"), "counts.csv")

    def test_nom_non_reconnu_n_est_pas_classe_au_hasard(self):
        self.assertIsNone(self.fetch.cible("facture_juillet.pdf"))


pac = charger("ctv_pacing/diagnose_pacing.py", "pacing_gate")


# --------------------------------------------------------------- chaîne de comptage
class TestChaineDeComptage(unittest.TestCase):
    """Un même trafic compté à plusieurs endroits. L'ordre déclaré est une hypothèse
    que la donnée peut contredire - et c'est ça qu'il faut voir."""

    def lignes(self, *tuples):
        """(plateforme, ssp, adserver, verif) par jour ; None = colonne absente."""
        out = []
        for n, t in enumerate(tuples):
            r = {"jour": j(-len(tuples) + n), "campagne": "c", "placement": "pl", "env": "web"}
            for col, v in zip(("imp_plateforme", "imp_ssp", "imp_adserver", "imp_verif"), t):
                r[col] = "" if v is None else str(v)
            out.append(r)
        return out

    def test_chaine_a_deux_points_reste_valide(self):
        # Rétrocompatibilité : un CSV sans SSP ni vérif produit un seul maillon.
        m = rec.analyser_chaine(self.lignes((100000, None, 94000, None)))
        self.assertEqual(len(m), 1)
        self.assertEqual((m[0][0], m[0][1]), ("imp_plateforme", "imp_adserver"))

    def test_ordre_contredit_quand_l_aval_compte_plus(self):
        # Le SSP est déclaré en aval de la plateforme mais compte 10 % de plus.
        m = rec.analyser_chaine(self.lignes((40000, 44000, 36000, 35000)))
        self.assertEqual(m[0][5], "ORDRE_CONTREDIT")
        self.assertIn("compté deux fois", m[0][6])

    def test_le_bruit_ne_suffit_pas_a_contredire_l_ordre(self):
        # 1 % au-dessus reste dans la tolérance : on n'accuse pas sur du bruit.
        m = rec.analyser_chaine(self.lignes((100000, 101000, 94000, 92000)))
        self.assertEqual(m[0][5], "ALIGNE")

    def test_ecart_materiel_entre_deux_maillons(self):
        m = rec.analyser_chaine(self.lignes((100000, 97000, 60000, 58000)))
        self.assertEqual(m[1][5], "ECART_MATERIEL")

    def test_colonne_vide_partout_est_ignoree(self):
        # Une source non renseignée ne doit pas créer un maillon à zéro,
        # qui se lirait comme un tag mort alors que c'est une colonne absente.
        m = rec.analyser_chaine(self.lignes((100000, None, 94000, None),
                                            (100000, None, 93000, None)))
        self.assertEqual(len(m), 1)

    def test_l_ordre_est_declarable(self):
        # Si l'ad server compte au win APRÈS le SSP, l'ordre change et le verdict avec.
        lignes = self.lignes((40000, 44000, 36000, 35000))
        m = rec.analyser_chaine(lignes, ordre=["imp_ssp", "imp_plateforme", "imp_adserver"])
        self.assertEqual(m[0][5], "ECART_MATERIEL")   # plus de contradiction d'ordre

    def test_la_chaine_voit_ce_que_le_couple_ne_voit_pas(self):
        # Le cas qui justifie l'ajout : plateforme <-> adserver est dans la bande CTV,
        # donc le verdict par couple dit « rien à négocier », alors que le SSP compte
        # plus que la plateforme. Sans la chaîne, ce placement passe inaperçu.
        lignes = self.lignes((40000, 44100, 36000, 34900), (41000, 45200, 36900, 35800))
        for r in lignes:
            r["env"] = "ctv"
        self.assertEqual(rec.diagnostiquer("c", lignes)[2], "STRUCTUREL_OK")
        self.assertEqual(rec.analyser_chaine(lignes)[0][5], "ORDRE_CONTREDIT")


# --------------------------------------------------------------- lecture économique
class TestLectureEconomique(unittest.TestCase):
    """Le sens de l'écart décide de sa nature. Et le gate n'invente pas la gouvernance."""

    TOTAUX = {"imp_plateforme": 1000000.0, "imp_ssp": 850000.0, "imp_adserver": 600000.0}

    def test_sans_declaration_le_gate_pose_la_question_au_lieu_d_y_repondre(self):
        v, d = rec.lecture_economique(self.TOTAUX)
        self.assertEqual(v, "GOUVERNANCE_NON_DECLAREE")
        self.assertIn("fait foi", d)

    def test_une_seule_des_deux_sources_ne_suffit_pas(self):
        v, _ = rec.lecture_economique(self.TOTAUX, source_facturation="imp_plateforme")
        self.assertEqual(v, "GOUVERNANCE_NON_DECLAREE")

    def test_facturer_plus_qu_on_achete_expose_au_litige_pas_a_la_marge(self):
        v, d = rec.lecture_economique(self.TOTAUX, "imp_plateforme", "imp_ssp")
        self.assertEqual(v, "RISQUE_SURFACTURATION")
        self.assertIn("défavorable au client", d)

    def test_acheter_plus_qu_on_facture_est_une_fuite_de_marge(self):
        v, d = rec.lecture_economique(self.TOTAUX, "imp_adserver", "imp_ssp")
        self.assertEqual(v, "FUITE_MARGE")
        self.assertIn("marge qui part", d)

    def test_deux_sources_alignees_ne_nomment_aucun_risque(self):
        v, _ = rec.lecture_economique({"a": 100000.0, "b": 99000.0}, "a", "b")
        self.assertEqual(v, "ALIGNE")

    def test_source_declaree_mais_absente_est_dite_absente_pas_a_zero(self):
        # Déclarer une source qui n'est pas dans le fichier doit se voir,
        # pas produire une division par zéro ni un faux verdict de fuite.
        v, _ = rec.lecture_economique(self.TOTAUX, "imp_plateforme", "imp_verif")
        self.assertEqual(v, "SOURCE_ABSENTE")


# --------------------------------------------------------------- bascule de comptage
class TestBasculeDePointDeComptage(unittest.TestCase):
    """Le jour où le bidder passe en event sourcing, l'écart est mécanique.
    Un gate qui crie à la panne ce jour-là détruit sa propre crédibilité."""

    def lignes(self, paires):
        return [{"jour": j(-len(paires) + n), "campagne": "c", "placement": "pl", "env": "web",
                 "imp_plateforme": str(p), "imp_adserver": str(a)}
                for n, (p, a) in enumerate(paires)]

    SERIE = [(100000, 92000), (100000, 91000), (100000, 60000)]

    def test_rupture_non_declaree_reste_une_panne(self):
        self.assertEqual(rec.diagnostiquer("c", self.lignes(self.SERIE))[2], "RUPTURE")

    def test_bascule_declaree_a_la_bonne_date_n_est_plus_une_panne(self):
        lignes = self.lignes(self.SERIE)
        v = rec.diagnostiquer("c", lignes, bascules={lignes[2]["jour"]: "event sourcing"})
        self.assertEqual(v[2], "BASCULE_ATTENDUE")
        self.assertIn("mécanique", v[3])

    def test_une_bascule_a_une_autre_date_ne_masque_pas_la_rupture(self):
        lignes = self.lignes(self.SERIE)
        v = rec.diagnostiquer("c", lignes, bascules={"1999-01-01": "sans rapport"})
        self.assertEqual(v[2], "RUPTURE")

    def test_la_bascule_n_invente_pas_une_rupture_absente(self):
        lignes = self.lignes([(100000, 92000), (100000, 91500)])
        v = rec.diagnostiquer("c", lignes, bascules={lignes[1]["jour"]: "event sourcing"})
        self.assertEqual(v[2], "STRUCTUREL_OK")

    def test_bascule_attendue_n_est_pas_comptee_a_investiguer(self):
        self.assertNotIn("BASCULE_ATTENDUE", rec.A_INVESTIGUER)


class TestOptionsReconcile(unittest.TestCase):
    def test_bascule_avec_raison(self):
        _, o = rec.parse_args(["f.csv", "--bascule", "2026-08-15=event sourcing"])
        self.assertEqual(o["bascules"], {"2026-08-15": "event sourcing"})

    def test_bascule_sans_raison_a_un_libelle_par_defaut(self):
        _, o = rec.parse_args(["f.csv", "--bascule", "2026-08-15"])
        self.assertTrue(o["bascules"]["2026-08-15"])

    def test_ordre_personnalise(self):
        _, o = rec.parse_args(["f.csv", "--ordre", "imp_ssp,imp_plateforme"])
        self.assertEqual(o["ordre"], ["imp_ssp", "imp_plateforme"])

    def test_option_inconnue_est_rejetee(self):
        with self.assertRaises(ValueError):
            rec.parse_args(["f.csv", "--nimporte-quoi"])

    def test_csv_manquant_est_rejete(self):
        with self.assertRaises(ValueError):
            rec.parse_args(["--facturation", "imp_ssp"])


# --------------------------------------------------------------- famille 6 : accès supply
class TestAccesSupply(unittest.TestCase):
    """L'inventaire qui n'existe pas et l'inventaire auquel on n'a pas droit sont
    deux causes différentes. Monter le bid ne règle que la seconde... et encore non :
    il ne règle ni l'une ni l'autre."""

    def lignes(self, **kw):
        base = {"cible": "9000", "livre": "4000", "win_rate": "0.45",
                "avails_reels": "40000", "avails_forecast": "42000",
                "latence_reporting_h": "2", "taux_consentement": "0.65"}
        base.update({k: str(v) for k, v in kw.items()})
        return [dict(base, campagne="c", jour=j(-3 + n),
                     livre=str(int(float(base["livre"]) - n * 100))) for n in range(3)]

    def test_inventaire_inaccessible_est_diagnostique_accès_pas_enchere(self):
        d = pac.diagnostiquer("c", self.lignes(avails_adressables=9000))
        self.assertIn("ACCÈS SUPPLY", d[2])
        self.assertIn("ne PAS monter l'enchère", d[4])

    def test_colonne_absente_ne_declenche_pas_la_famille(self):
        # Rétrocompatibilité : les exports sans la colonne gardent leur diagnostic.
        d = pac.diagnostiquer("c", self.lignes())
        self.assertNotIn("ACCÈS SUPPLY", d[2])

    def test_inventaire_ouvert_ne_declenche_pas_la_famille(self):
        d = pac.diagnostiquer("c", self.lignes(avails_adressables=38000))
        self.assertNotIn("ACCÈS SUPPLY", d[2])

    def test_l_acces_passe_avant_l_enchere(self):
        # Les deux signaux présents : c'est l'accès qui doit être la famille primaire,
        # parce qu'un bid plus haut sur un inventaire fermé ne produit rien.
        d = pac.diagnostiquer("c", self.lignes(avails_adressables=9000, win_rate=0.03))
        self.assertIn("ACCÈS SUPPLY", d[2])
        self.assertTrue(any("ENCHÈRE" in s for s in d[6]), "l'enchère reste signalée en second")

    def test_avails_a_zero_ne_divise_pas(self):
        d = pac.diagnostiquer("c", self.lignes(avails_reels=0, avails_adressables=0))
        self.assertIsNotNone(d)


class TestPiegesDeLaLectureGlobale(unittest.TestCase):
    """Trouvés à l'audit : deux façons pour ce gate de se tromper en silence."""

    def rows(self):
        return [
            {"env": "web", "imp_plateforme": "100000", "imp_ssp": "97000"},
            {"env": "dooh", "imp_plateforme": "400000", "imp_ssp": ""},
        ]

    def test_le_dooh_ne_pollue_pas_le_total_qui_sert_a_facturer(self):
        # Le DOOH porte un multiplicateur d'impressions. L'agréger dans la lecture
        # financière referait, sur le total et sans le dire, l'erreur que le verdict
        # DOOH_NON_COMPARABLE existe pour empêcher placement par placement.
        avec = rec.totaux_par_source(self.rows())["imp_plateforme"]
        sans = rec.totaux_par_source(self.rows(), exclure_dooh=True)["imp_plateforme"]
        self.assertEqual(avec, 500000.0)
        self.assertEqual(sans, 100000.0)

    def test_une_option_sans_valeur_est_une_erreur_lisible(self):
        # Sans ça : IndexError et une stack trace, au lieu d'un message.
        for opt in ("--ordre", "--facturation", "--achat", "--pilotage", "--bascule"):
            with self.assertRaises(ValueError, msg=f"{opt} en fin de ligne"):
                rec.parse_args(["f.csv", opt])


if __name__ == "__main__":
    unittest.main()
