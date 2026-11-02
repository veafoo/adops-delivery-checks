#!/usr/bin/env bash
# send_briefing.sh — routine 7h30 : checks -> PDF -> mailing list. Cron-able.
#
#   crontab -e :
#   30 7 * * 1-5 /chemin/scripts/send_briefing.sh /data/delivery "trader@x.com,lead@x.com"
#
# Variables : DATA (dossier des exports), LISTE (destinataires), OUTDIR (archive des briefings).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${1:-.}"; LISTE="${2:-${BRIEFING_LISTE:-}}"; OUTDIR="${OUTDIR:-$DATA/briefings}"
JOUR="$(date +%F)"; mkdir -p "$OUTDIR"

# Recuperation des exports de la veille depuis la boite mail, si configuree.
# Sans BRIEFING_IMAP_HOST on suppose que les fichiers sont deja dans $DATA.
if [ -n "${BRIEFING_IMAP_HOST:-}" ]; then
  python3 "$HERE/fetch_reports.py" "$DATA" --depuis 1 \
    ${BRIEFING_IMAP_FROM:+--expediteur "$BRIEFING_IMAP_FROM"} || \
    echo "Recuperation IMAP sans nouveau fichier : on continue sur les donnees presentes." >&2
fi
HTML="$OUTDIR/briefing-$JOUR.html"; PDF="$OUTDIR/briefing-$JOUR.pdf"; MD="$OUTDIR/briefing-$JOUR.md"

set +e
python3 "$HERE/morning_check.py" "$DATA" --html "$HTML" --md "$MD" --quiet
CODE=$?           # 0 = RAS, 2 = au moins un P1
set -e
"$HERE/html_to_pdf.sh" "$HTML" "$PDF" >/dev/null

SUJET="Briefing delivery $JOUR"
[ "$CODE" -eq 2 ] && SUJET="[ACTION] $SUJET" || SUJET="[RAS] $SUJET"
[ -z "$LISTE" ] && { echo "Pas de mailing list : briefing dans $OUTDIR"; exit "$CODE"; }

# La liste de destinataires vient de l'appelant (cron, CI, argument). On la
# decoupe dans un tableau au lieu de laisser le shell re-decouper une
# substitution de commande : `$(echo ... | tr)` non quote permettrait a une
# adresse forgee du type `a@b.com;rm -rf ~` de s'executer.
IFS=',' read -r -a DESTS <<< "$LISTE"
for d in "${DESTS[@]}"; do
  case "$d" in
    *[!A-Za-z0-9._%+@-]*|"") echo "Destinataire invalide, envoi annule : '$d'" >&2; exit 1 ;;
  esac
done

# 1) mutt/mailx si dispo (SMTP local ou msmtp configure)
if command -v mutt >/dev/null; then
  mutt -s "$SUJET" -a "$PDF" -- "${DESTS[@]}" < "$MD"
elif command -v mailx >/dev/null; then
  mailx -s "$SUJET" -a "$PDF" "${DESTS[@]}" < "$MD"
else
  echo "Ni mutt ni mailx : PDF prêt dans $PDF, à joindre manuellement (ou installer msmtp+mutt)." >&2
fi
echo "$SUJET -> $LISTE ($PDF)"
exit "$CODE"
