# Briefing delivery - 2026-07-26

**6 P1** · 6 P2 · 7 campagnes en cours · 2 à extraire

- Pacing dans la bande : 85.7 % (6/7 campagnes)
- Budget consommé vs prévu à date : 107.4 %
- Marge réalisée : 24.7 % (soit 113.1 % du floor)
- Comptage conforme : 60.0 % (3/5 placements)
- Objectifs atteints : 87.5 %
- Mise en place : 45.5 %

| Prio | Campagne | Canal | Constat et action |
|---|---|---|---|
| P1 | fashion-it-inapp | inapp | taux de landing à 0.3163 contre une cible de 0.9 (35.1 % de l'objectif) |
| P1 | fashion-it-inapp | inapp | 68.4 % des clics n'atteignent pas la landing : chaîne de redirect ou tag - lint check_tracking_url.py |
| P1 | newco-fr-display | setup | tâche BLOQUÉE : Tags adserver annonceur poses et testes (Lead delivery) |
| P1 | newco-fr-display | setup | go-live dans 2 j et setup à 40.0 % (3 tâche(s) restante(s)) |
| P1 | bank-be | ctv | RUPTURE de comptage sur pl-ctv-spot (73.2%) - changement de régime le 2026-07-21 (ratio 0.92 -> 0.65) : mode de comptage, tag modifié, ou chute de consentement ce jour-là |
| P1 | retail-fr | inapp | TAG MORT sur pl-inapp-inter : l'ad server annonceur ne compte quasi rien (0.4%) - chaque heure qui passe perd de la donnée, recette du placement |
| P2 | auto-de-video | video | sous la bande video (87.5 % vs 88.0 %) - à surveiller aujourd'hui |
| P2 | bank-be-ctv | ctv | sur-livraison (150.0 %) - budget consommé trop vite, fin de vol à vide |
| P2 | resto-fr-dooh | dooh | sur-livraison (133.8 %) - budget consommé trop vite, fin de vol à vide |
| P2 | fashion-it-inapp | inapp | marge 13.2 % sous le floor 20.0 % |
| P2 | resto-fr-dooh | setup | tâche en retard de 2 j : Boucles et part de voix validees |
| P2 | bank-be-ctv | ctv | placement BVOD-premium sur-livre (161.9 %) : il mange le budget des autres |

## Placements hors bande (l'agrégé les masque)

- **auto-de-video** / Instream-premium (video) : 75.0 % de la courbe
- **retail-fr-display-q3** / Retargeting-300x250 (display) : 86.0 % de la courbe

## Campagnes terminées (extraction report)

- **beaute-fr-display** (Beaute Theta, display) fin 2026-07-24 - atteinte 99.2 %
- **boisson-be-video** (Boisson Iota, video) fin 2026-07-25 - atteinte 99.4 %

## Checks non exécutés

- URLs de tracking : `tracking_urls.txt` absent
- Pacing CTV détaillé (5 familles) : `delivery_export.csv` absent