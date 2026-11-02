#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Régression : morning_check.py retrouve les scripts voisins dans l'arborescence du dépôt.

Avant la mise à plat, les scripts vivaient deux niveaux plus bas ; si RACINE pointe
au mauvais niveau, l'import de la réconciliation retombe silencieusement sur None et les
gates chaînés ne trouvent plus leur script.

    python3 -m unittest discover -s tests -v
"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
SCRIPT = RACINE / "morning_briefing" / "morning_check.py"
DEMO = RACINE / "morning_briefing" / "demo"

spec = importlib.util.spec_from_file_location("mc_layout", SCRIPT)
mc = importlib.util.module_from_spec(spec)
sys.modules["mc_layout"] = mc
spec.loader.exec_module(mc)


class TestCheminsDuDepot(unittest.TestCase):
    def test_racine_est_la_racine_du_depot(self):
        self.assertEqual(mc.RACINE, RACINE)
        self.assertTrue((mc.RACINE / "discrepancy").is_dir())

    def test_la_reconciliation_est_importee(self):
        self.assertIsNotNone(mc.REC, "reconcile_counts.py introuvable depuis morning_check.py")
        self.assertTrue(hasattr(mc.REC, "analyser_chaine"))

    def test_les_gates_chaines_existent(self):
        self.assertTrue(mc.GATES)
        for titre, _fichier, chemin, _marks in mc.GATES:
            self.assertTrue(Path(chemin).is_file(), f"gate absent : {titre} -> {chemin}")

    def test_aucun_chemin_de_l_ancienne_arborescence(self):
        src = SCRIPT.read_text(encoding="utf-8")
        for ancien in ("HARNESS", "skills/"):
            self.assertNotIn(ancien, src)

    def test_le_briefing_execute_les_gates_chaines(self):
        # Les trois démos sont dans trois dossiers : on les rassemble comme le ferait un dossier de données.
        with tempfile.TemporaryDirectory() as tmp:
            for f in DEMO.glob("*.csv"):
                shutil.copy(f, tmp)
            shutil.copy(RACINE / "discrepancy/demo/tracking_urls.txt", tmp)
            shutil.copy(RACINE / "ctv_pacing/demo/delivery_export.csv", tmp)
            r = subprocess.run([sys.executable, str(SCRIPT), tmp, "--md", "/dev/stdout", "--quiet"],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("## URLs de tracking", r.stdout)
        self.assertIn("## Pacing CTV détaillé", r.stdout)
        self.assertNotIn("verdict indisponible", r.stdout)
        self.assertNotIn("Checks non exécutés", r.stdout)


if __name__ == "__main__":
    unittest.main()
