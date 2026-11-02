# adops-delivery-checks

Small command-line checks for ad operations: campaign pacing, count reconciliation between
ad servers, tracking-URL linting, and macro translation between ad platforms.
Python 3, standard library only. Code comments and output messages are in French.

Every check is a plain script: CSV or text in, findings out, exit code 0 when clean and
2 when something needs a look (1 = bad usage). Demo data is synthetic; advertisers are made up.

## Scripts

| Folder | Script | What it checks |
|---|---|---|
| `morning_briefing/` | `morning_check.py` | Daily briefing across channels: pacing against the delivery curve, CTR / landing / conversion / CPA against targets, margin against floor, setup tasks, campaigns ending. Chains the checks below. Writes Markdown and printable HTML. |
| | `fetch_reports.py` | Pulls the daily exports from a mailbox (IMAP) and renames attachments to the file names above. |
| | `html_to_pdf.sh`, `send_briefing.sh` | HTML to PDF (Chrome, wkhtmltopdf or weasyprint), then mail it. Cron-friendly. |
| `discrepancy/` | `reconcile_counts.py` | Counts along the chain platform, SSP, advertiser ad server, verification vendor. Flags ratios outside the band for the environment (web, in-app, CTV), order contradictions and regime breaks. |
| | `check_tracking_url.py` | Static lint of tracking and redirect URLs: unresolved macros, encoding depth, param bleed, duplicate parameters, missing cachebuster. |
| | `impression_log_probe.py` | Impression-level log: duplicate IDs, empty referrers, unresolved macros, consent rate, bots, non-200 status. |
| `ctv_pacing/` | `diagnose_pacing.py` | CTV under-delivery: points to one of six cause families (supply, controller, bid, measurement, consent, supply access) with the numbers behind it. |
| `adserver_macros/` | `translate.py` | Translates a tag between platforms by keeping each macro's role and rewriting its syntax. Warns on macros the target does not expose. |
| | `find_unsubstituted.py` | Finds macros that went out unsubstituted in logs, exports or URL lists. |
| | `macros.py` | The macro table used by the two scripts above. |

## Run on the demo data

```
python3 morning_briefing/morning_check.py morning_briefing/demo --md briefing.md --html briefing.html
python3 discrepancy/reconcile_counts.py discrepancy/demo/counts.csv
python3 discrepancy/check_tracking_url.py discrepancy/demo/tracking_urls.txt
python3 discrepancy/impression_log_probe.py discrepancy/demo/impressions.csv
python3 ctv_pacing/diagnose_pacing.py ctv_pacing/demo/delivery_export.csv
python3 adserver_macros/translate.py --from cm360 --to dv360 --file adserver_macros/demo/tags.txt
python3 adserver_macros/find_unsubstituted.py adserver_macros/demo/appels_reels.txt
```

The demo data contains deliberate problems, so most of these exit 2. `exemple-sortie-*.txt` files
in each `demo/` folder show the expected output. `morning_briefing/demo/exemple-briefing.*` is a
snapshot taken on 2026-07-26: pacing depends on today's date, so a fresh run differs.

`morning_check.py` reads these files from the data folder, each optional and reported when
missing: `campaigns.csv`, `board.csv`, `counts.csv`, `tracking_urls.txt`, `delivery_export.csv`.
Column layouts are in the demo files and in each script's docstring.

## Exit codes

- 0: nothing to do
- 2: at least one finding (for the briefing: at least one P1)
- 1: bad usage or unreadable input
- `fetch_reports.py`: 0 files written, 3 nothing new, 1 connection or config error

## Network

Everything works offline except two places, both opt-in:
- `fetch_reports.py` opens an IMAP connection. Host, user and password come from
  `BRIEFING_IMAP_HOST`, `BRIEFING_IMAP_USER`, `BRIEFING_IMAP_PASS`.
- `check_tracking_url.py --follow` follows redirects over HTTP. Without the flag it is offline.

`send_briefing.sh` calls `mutt` or `mailx` if installed.

## Tests

```
python3 -m unittest discover -s tests
```

Covers colour semantics, pacing arithmetic at the bounds, the CSV reader, URL lint,
reconciliation chain, macro library and translation, and end-to-end runs on the demo data.
The tests need no network.

## License

MIT, see `LICENSE`.
