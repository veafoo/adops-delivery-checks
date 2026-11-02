#!/usr/bin/env bash
# html_to_pdf.sh <in.html> <out.pdf> — rendu PDF sans dépendance Python.
# Essaie, dans l'ordre : Chrome/Chromium headless, wkhtmltopdf, weasyprint.
set -euo pipefail
IN="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; OUT="$2"
for C in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium" \
         "$(command -v google-chrome || true)" "$(command -v chromium || true)"; do
  [ -x "$C" ] || continue
  "$C" --headless --disable-gpu --no-pdf-header-footer --print-to-pdf="$OUT" "file://$IN" 2>/dev/null && \
    { echo "PDF: $OUT (chrome)"; exit 0; }
done
command -v wkhtmltopdf >/dev/null && { wkhtmltopdf -q "$IN" "$OUT"; echo "PDF: $OUT (wkhtmltopdf)"; exit 0; }
command -v weasyprint  >/dev/null && { weasyprint "$IN" "$OUT"; echo "PDF: $OUT (weasyprint)"; exit 0; }
echo "Aucun moteur de rendu trouvé (Chrome / wkhtmltopdf / weasyprint)." >&2; exit 1
