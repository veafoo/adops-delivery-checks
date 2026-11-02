#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macros.py — bibliothèque de macros d'adserving et table de correspondance.

Base de connaissance pure (aucun effet de bord), utilisée par translate.py.
Chaque plateforme déclare la syntaxe qu'elle SUBSTITUE dans un tag sortant.

Le concept qui structure tout : une macro est un couple (rôle, syntaxe).
Le rôle est universel — « identifiant de cache », « position de l'écran » —
la syntaxe est propre à la plateforme. Traduire un redirect, c'est conserver
les rôles et réécrire les syntaxes.

RÔLES COUVERTS
  cachebuster, click_url, click_id, campaign_id, creative_id, placement_id,
  site_id, line_item_id, insertion_order_id, advertiser_id, deal_id, device_id,
  ifa, ifa_type, lat, lon, geo, country, gdpr, gdpr_consent, us_privacy, gpp,
  gpp_sid, app_bundle, app_name, site_domain, page_url, referrer, media_type,
  width, height, timestamp, random, venue_id, screen_id, plays,
  impression_multiplier, auction_id, auction_price, publisher_id, user_agent,
  ip, lmt, dnt, sound_state

PROVENANCE — pourquoi ce champ existe
  Une table de macros construite de mémoire a l'air identique à une table
  vérifiée. Elle ne vaut pas la même chose, et rien dans le fichier ne le
  disait. Chaque plateforme porte donc `_statut` et, quand il existe, `_source`
  (l'URL de la doc éditeur consultée) et `_verifie_le`.

    VERIFIE_DOC  la table a été confrontée à la doc éditeur citée dans `_source`.
    NON_VERIFIE  table issue de la pratique, jamais reconfrontée à la doc.
                 Utilisable pour se repérer, à vérifier avant une mise en ligne.
    NON_PUBLIC   la doc de la plateforme n'est pas accessible publiquement.
                 L'outil ne devine pas : il déclare le trou et n'expose rien.

  Un statut n'est pas un jugement de qualité, c'est l'origine de la donnée.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# Chaque plateforme : rôle -> syntaxe substituée dans un tag SORTANT.
# `None` = la plateforme n'expose pas ce rôle (l'information n'existe pas de
# son côté) : c'est une donnée utile, pas un trou à combler.
# --------------------------------------------------------------------------
PLATEFORMES: dict[str, dict[str, str | None]] = {
    # ---------------------------------------------------------------- Google
    "gam": {  # Google Ad Manager (ex-DFP), tags GPT et tags tiers
        "_libelle": "Google Ad Manager",
        "_statut": "NON_VERIFIE",
        "_note": "issue de la pratique ; macros app/mobile non renseignees",
        "cachebuster": "%%CACHEBUSTER%%",
        "click_url": "%%CLICK_URL_UNESC%%",
        "click_url_escaped": "%%CLICK_URL_ESC%%",
        "campaign_id": "%eaid!",
        "creative_id": "%ecid!",
        "placement_id": "%epid!",
        "site_id": "%esid!",
        "advertiser_id": "%eadv!",
        "line_item_id": "%eaid!",
        "page_url": "%%PATTERN:url%%",
        "referrer": "%%REFERRER_URL_UNESC%%",
        "width": "%%WIDTH%%",
        "height": "%%HEIGHT%%",
        "timestamp": "%%TIMESTAMP%%",
        "gdpr": "${GDPR}",
        "gdpr_consent": "${GDPR_CONSENT_755}",
        "us_privacy": "${US_PRIVACY}",
        "gpp": "${GPP_STRING}",
        "gpp_sid": "${GPP_SID}",
        "deal_id": None,
        "auction_price": None,
    },
    "cm360": {  # Campaign Manager 360 — l'ad server annonceur de référence
        "_libelle": "Campaign Manager 360",
        "_statut": "NON_VERIFIE",
        "_note": "issue de la pratique ; macros app/mobile non renseignees",
        "cachebuster": "%n",              # nombre aléatoire, anti-cache
        "click_url": "%c",                # chaîne de clic à préfixer
        "campaign_id": "%eaid!",
        "creative_id": "%ecid!",
        "placement_id": "%epid!",
        "site_id": "%esid!",
        "advertiser_id": "%eadv!",
        "page_url": "%u",                 # URL de la page (référent)
        "referrer": "%r",
        "width": "%eweight!",
        "height": "%eheight!",
        "timestamp": "%t",
        "random": "%n",
        "gdpr": "${GDPR}",
        "gdpr_consent": "${GDPR_CONSENT_755}",
        "us_privacy": "${US_PRIVACY}",
        "device_id": None,
        "deal_id": None,
    },
    "dv360": {  # Display & Video 360 — côté achat
        "_libelle": "Display & Video 360",
        "_statut": "NON_VERIFIE",
        "_note": "couverture la plus complete de la table, issue de la pratique",
        "cachebuster": "${CACHEBUSTER}",
        "click_url": "${CLICK_URL_UNESC}",
        "click_url_escaped": "${CLICK_URL_ENC}",
        "campaign_id": "${CAMPAIGN_ID}",
        "creative_id": "${CREATIVE_ID}",
        "line_item_id": "${LINE_ITEM_ID}",
        "insertion_order_id": "${INSERTION_ORDER_ID}",
        "advertiser_id": "${ADVERTISER_ID}",
        "placement_id": "${PLACEMENT_ID}",
        "site_id": "${SITE_ID}",
        "publisher_id": "${PUBLISHER_ID}",
        "deal_id": "${DEAL_ID}",
        "device_id": "${DEVICE_ID_MD5}",
        "ifa": "${ADVERTISING_IDENTIFIER}",
        "ifa_type": "${ADVERTISING_IDENTIFIER_TYPE}",
        "lat": "${LATITUDE}",
        "lon": "${LONGITUDE}",
        "country": "${COUNTRY}",
        "app_bundle": "${APP_BUNDLE}",
        "app_name": "${APP_NAME}",
        "site_domain": "${SITE}",
        "page_url": "${SOURCE_URL_ENC}",
        "media_type": "${MEDIA_TYPE}",
        "width": "${WIDTH}",
        "height": "${HEIGHT}",
        "timestamp": "${TIMESTAMP}",
        "random": "${CACHEBUSTER}",
        "auction_id": "${AUCTION_ID}",
        "auction_price": "${AUCTION_PRICE}",
        "gdpr": "${GDPR}",
        "gdpr_consent": "${GDPR_CONSENT_755}",
        "us_privacy": "${US_PRIVACY}",
        "gpp": "${GPP_STRING}",
        "gpp_sid": "${GPP_SID}",
        "lmt": "${LIMIT_AD_TRACKING}",
        "user_agent": None,
        "ip": None,
    },
    "google_ads": {
        # ATTENTION au contresens : ValueTrack n'est PAS une famille de macros
        # d'adserving. Ce sont des paramètres de suivi substitués dans le
        # modèle de tracking d'une URL de destination. Google Ads ne sert pas
        # de tag tiers de diffusion : il n'y a ni chaîne de clic à préfixer,
        # ni macro de consentement. Le rapprochement de rôles ci-dessous est
        # une commodité de lecture, pas une équivalence fonctionnelle.
        "_libelle": "Google Ads (ValueTrack)",
        "_statut": "VERIFIE_DOC",
        "_source": "https://support.google.com/google-ads/answer/6305348",
        "_verifie_le": "2026-07-27",
        "_note": "parametres d'URL de suivi, pas des macros de diffusion (voir commentaire)",
        "cachebuster": "{random}",        # nombre généré par Google
        "click_id": "{gclid}",            # l'identifiant qui porte l'attribution
        "click_url": "{lpurl}",           # URL finale, base du modèle de suivi
        "page_url": "{lpurl}",
        "campaign_id": "{campaignid}",
        "creative_id": "{creative}",      # ID de l'annonce
        "line_item_id": "{adgroupid}",    # le groupe d'annonces tient ce rôle
        "site_domain": "{placement}",     # le SITE de diffusion, pas un ID de pavé
        "geo": "{loc_physical_ms}",       # ID de critère géographique, pas un pays
        "media_type": "{network}",
        "placement_id": None,             # n'existe pas : {placement} est un domaine
        "creative_id_tiers": None,
        "timestamp": None,
        "gdpr": None,
        "gdpr_consent": None,
        "deal_id": None,
        "device_id": None,
    },
    # ---------------------------------------------------------------- DSP
    "ttd": {  # The Trade Desk
        "_libelle": "The Trade Desk",
        "_statut": "NON_VERIFIE",
        "_note": "issue de la pratique ; placement_id et GPP a confirmer",
        "cachebuster": "%%TTD_CACHEBUSTER%%",
        "click_url": "%%TTD_CLK_ESC%%",
        "campaign_id": "%%TTD_CAMPAIGNID%%",
        "creative_id": "%%TTD_CREATIVEID%%",
        "advertiser_id": "%%TTD_ADVERTISERID%%",
        "deal_id": "%%TTD_DEALID%%",
        "device_id": "%%TTD_DEVICEID%%",
        "ifa": "%%TTD_DEVICEID%%",
        "lat": "%%TTD_LAT%%",
        "lon": "%%TTD_LON%%",
        "country": "%%TTD_COUNTRY%%",
        "app_bundle": "%%TTD_APPBUNDLEID%%",
        "site_domain": "%%TTD_SITE%%",
        "page_url": "%%TTD_REFERERURL%%",
        "media_type": "%%TTD_CHANNEL%%",
        "timestamp": "%%TTD_TIMESTAMP%%",
        "gdpr": "%%TTD_GDPR%%",
        "gdpr_consent": "%%TTD_GDPR_CONSENT%%",
        "us_privacy": "%%TTD_US_PRIVACY%%",
        "auction_id": "%%TTD_IMPRESSIONID%%",
    },
    "xandr": {  # Xandr / Microsoft Invest (ex-AppNexus)
        "_libelle": "Xandr (Microsoft Invest)",
        "_statut": "NON_VERIFIE",
        "_note": "issue de la pratique ; CLICK_URL et CACHEBUSTER corrobores par la doc Microsoft",
        "cachebuster": "${CACHEBUSTER}",
        "click_url": "${CLICK_URL}",
        "click_url_escaped": "${CLICK_URL_ENC}",
        "campaign_id": "${CAMP_ID}",
        "creative_id": "${CREATIVE_ID}",
        "placement_id": "${TAG_ID}",
        "advertiser_id": "${ADVERTISER_ID}",
        "line_item_id": "${LINE_ITEM_ID}",
        "deal_id": "${DEAL_ID}",
        "device_id": "${DEVICE_ID}",
        "ifa": "${DEVICE_IFA}",
        "lat": "${DEVICE_LAT}",
        "lon": "${DEVICE_LON}",
        "country": "${COUNTRY_CODE}",
        "app_bundle": "${APP_BUNDLE}",
        "site_domain": "${REFERRER_DOMAIN}",
        "page_url": "${REFERRER_URL}",
        "media_type": "${MEDIA_TYPE}",
        "width": "${WIDTH}",
        "height": "${HEIGHT}",
        "auction_price": "${AUCTION_PRICE}",
        "auction_id": "${AUCTION_ID}",
        "user_agent": "${USER_AGENT}",
        "ip": "${IP_ADDRESS}",
        "gdpr": "${GDPR}",
        "gdpr_consent": "${GDPR_CONSENT_STRING}",
        "us_privacy": "${US_PRIVACY}",
    },
    "amazon_dsp": {
        # La table précédente exposait $CACHEBUSTER$, $CLICK_URL$, $CAMPAIGN_ID$…
        # Aucune de ces syntaxes n'a pu être retrouvée dans une source éditeur :
        # la doc macros d'Amazon DSP vit derrière l'authentification du centre
        # d'aide advertising.amazon.com. Elles sont retirées plutôt que
        # conservées : une syntaxe fausse dans un outil de traduction produit
        # un tag qui part en production avec une macro qui ne sera jamais
        # substituée. Déclarer le trou coûte moins cher que le combler au juger.
        "_libelle": "Amazon DSP",
        "_statut": "NON_PUBLIC",
        "_note": "doc macros derriere authentification : rien n'est expose, a recuperer aupres du contact Amazon",
    },
    "amazon_mmp": {
        # La SEULE famille de macros Amazon documentée sur une page publique
        # (sans authentification) : les URL de mesure passées à un MMP en
        # publicité applicative. Famille en double tiret bas, deux préfixes :
        # __CS_* (côté campagne) et __AAX_* (côté place de marché Amazon).
        #
        # PÉRIMÈTRE, à lire avant de s'en servir : ces macros valent pour les
        # URL de mesure d'app, PAS pour un tag créa tiers de diffusion. Ne pas
        # en déduire les macros créa d'Amazon DSP, qui restent derrière
        # authentification (voir la plateforme `amazon_dsp`).
        #
        # Beaucoup de ces macros substituent un NOM, pas un identifiant. Elles
        # sont donc listées hors rôles plutôt que rattachées au juger : mapper
        # __CS_CAMPAIGN_NAME__ sur le rôle campaign_id ferait passer un nom
        # pour un ID dans une traduction, ce qui est précisément le genre
        # d'approximation que cette bibliothèque existe pour empêcher.
        "_libelle": "Amazon Ads (URL de mesure MMP, app)",
        "_statut": "VERIFIE_DOC",
        "_source": "https://advertising.amazon.com/resources/ad-policy/mmp-measurement-urls",
        "_verifie_le": "2026-08-01",
        "_note": "perimetre mesure d'app uniquement ; famille __CS_*__ / __AAX_*__ ; pas des macros crea",
        "creative_id": "__CS_CREATIVE_ID__",
        "ifa": "__AAX_IDFA__",              # Fire tablet / Fire TV
        "device_id": "__AAX_SHA1_UDID__",   # identifiant Android haché en SHA1
        "cachebuster": None,                # aucun cachebuster documenté sur cette surface
        "click_url": None,
        "campaign_id": None,                # __CS_CAMPAIGN_NAME__ est un nom, pas un ID
        "deal_id": None,
        "gdpr_consent": None,
        # Macros documentées dont la valeur ne correspond à aucun rôle du
        # vocabulaire (noms, coûts, plomberie du postback). Conservées
        # verbatim : elles servent à RECONNAÎTRE une macro non substituée
        # dans une URL de mesure, pas à en fabriquer une.
        "_hors_roles": {
            "__CS_CAMPAIGN_NAME__": "ID + nom de la campagne Amazon",
            "__CS_AD_NAME__": "ID + nom de la publicite Amazon",
            "__CS_AD_SIZE__": "taille de la creation",
            "__CS_APP_ID__": "ID d'application Amazon de l'app promue",
            "__CS_MSR_PARTNER__": "identifiant du partenaire de mesure (MMP)",
            "__AAX_SOURCE_NAME__": "groupe d'editeurs Amazon",
            "__AAX_SITE_NAME__": "editeur Amazon",
            "__AAX_AD_TRACKING__": "indicateur de suivi publicitaire de l'appareil",
            "__AAX_TRACKING_ID__": "reference de tracking (referent)",
            "__AAX_COST_MODEL__": "modele de cout",
            "__AAX_COST__": "montant du cout de campagne",
            "__AAX_COST_CURRENCY__": "devise du cout",
            "__AAX_POSTBACK_URL__": "endpoint de postback regional",
        },
    },
    "equativ_adserver": {
        # Equativ (ex-Smart AdServer), côté ad server / publisher.
        # Famille en crochets, préfixe sas_ pour les identifiants.
        # Filtres disponibles : [macroName|filterName], ex. [sas_userAgent|urlEncoded].
        "_libelle": "Equativ Ad Server (ex-Smart)",
        "_statut": "VERIFIE_DOC",
        "_source": "https://help.smartadserver.com/articles/Knowledge/Macros",
        "_verifie_le": "2026-07-27",
        "_note": "famille [crochets] cote ad server ; filtres [macro|filtre] ; ne pas confondre avec Activation",
        "cachebuster": "[timestamp]",
        "click_url": "[countgo]",
        "click_url_escaped": "[countgoEncoded]",
        "campaign_id": "[sas_campaignId]",
        "creative_id": "[sas_creativeId]",
        "insertion_order_id": "[sas_insertionId]",
        "site_id": "[sas_siteId]",
        "advertiser_id": "[sas_advertiserId]",
        "placement_id": "[sas_formatId]",
        "timestamp": "[timestamp]",
        "user_agent": "[sas_userAgent|urlEncoded]",
        "deal_id": None,
        "gdpr": None,             # le consentement est porté par Activation
        "gdpr_consent": None,
    },
    "equativ_activation": {
        # Equativ côté achat (module Activation, macros de line item).
        # Famille en accolades. ATTENTION à la casse : les macros de
        # consentement sont en minuscules, les macros contextuelles en
        # majuscules, et les macros DSA prennent la forme ${MACRO}.
        # Recopier verbatim : {gdpr} et {GDPR} ne sont pas interchangeables.
        "_libelle": "Equativ Activation (achat)",
        "_statut": "VERIFIE_DOC",
        "_source": "https://help.equativ.com/create-line-item-macros",
        "_verifie_le": "2026-07-27",
        "_note": "famille {accolades} ; casse significative ; DSA en ${MACRO}",
        "cachebuster": "{timestamp}",
        "timestamp": "{timestamp}",
        "click_url": "{click_url}",
        "click_url_escaped": "{click_url_enc}",
        "campaign_id": "{CAMPAIGN_ID}",
        "creative_id": "{CREATIVE_ID}",
        "advertiser_id": "{ADVERTISER_ID}",
        "line_item_id": "{LINEITEM_ID}",
        "deal_id": "{DEAL_ID}",
        "publisher_id": "{PUBLISHER_ID}",
        "site_id": "{SITE_ID}",
        "site_domain": "{SITE_DOMAIN}",
        "page_url": "{SITE_PAGE}",
        "app_bundle": "{APP_BUNDLE}",
        "app_name": "{APP_NAME}",
        "width": "{PLAYER_WIDTH}",
        "height": "{PLAYER_HEIGHT}",
        "gdpr": "{gdpr}",
        "gdpr_consent": "{gdpr_consent}",
        "gpp": "{gpp_string}",
        "gpp_sid": "{gpp_sid}",
        "us_privacy": None,
    },
    "adform": {
        # Famille __ADF*__ en double tiret bas. Adform accepte %%ADF*%% comme
        # séparateur équivalent, mais sa propre doc recommande __ pour éviter
        # les collisions d'encodage d'URL — on suit la recommandation.
        # Le clic ne passe PAS par cette table : Adform insère sa chaîne de
        # clic via la commande de tag `%CLICK;cpdir=`, qui n'est pas une macro
        # de la liste ci-dessous (source : guide des tags tiers Adform).
        # À SAVOIR AVANT DE TRAFFIQUER (source : doc ad-serving, 01/08/2026) :
        # Adform ENCODE toutes les valeurs qu'il substitue. Une chaîne de clic
        # passée par une macro Adform ressort donc déjà encodée, et la
        # ré-encoder est une cause classique de redirection cassée. C'est
        # l'inverse de la précaution habituelle sur les rôles de type URL.
        "_libelle": "Adform",
        "_statut": "VERIFIE_DOC",
        "_source": "https://www.adformhelp.com/hc/en-us/articles/9739143043345-Overview-of-Supported-Ad-Serving-Macros",
        "_verifie_le": "2026-07-27",
        "_note": "famille __ADF*__ ; %%ADF*%% equivalent mais deconseille par l'editeur ; clic = commande %CLICK;cpdir=",
        "cachebuster": "__ADFRND__",
        "random": "__ADFRND__",
        "timestamp": "__ADFTIMESTAMP__",
        "click_url": "%CLICK;cpdir=",     # commande de tag, pas une macro de la table
        "click_id": "__ADFTPC_gclid__",   # motif __ADFTPC_*__ : fbclid, gclid, dclid, msclkid, ttclid
        "campaign_id": "__ADFCAMID__",
        "creative_id": "__ADFCREID__",
        "placement_id": "__ADFPLAID__",   # l'ID de placement est aussi l'ID de line item
        "line_item_id": "__ADFPLAID__",
        "insertion_order_id": "__ADFORDID__",
        "advertiser_id": "__ADFADVID__",
        "publisher_id": "__ADFPUBID__",
        "deal_id": "__ADFDPID__",
        "site_id": "__ADFMEDID__",
        "site_domain": "__ADFBNRDOM__",
        "page_url": "__ADFPAGEURL__",
        "referrer": "__ADFREFERER__",
        "app_bundle": "__ADFAPPID__",
        "app_name": "__ADFAPPNAME__",
        "device_id": "__ADFDEVID__",
        "ifa": "__ADFDEVID__",
        "lat": "__ADFLAT__",
        "lon": "__ADFLNG__",
        "country": "__ADFCOUNTRY__",
        "geo": "__ADFREGION__",
        "width": "__ADFWIDTH__",
        "height": "__ADFHEIGHT__",
        "media_type": "__ADFTYP__",
        "auction_id": "__ADFIMPID__",     # identifiant d'impression, stable clic/event
        "gdpr": "__ADFGDPR__",
        "gdpr_consent": "__ADFGDPRCONSENT__",
        "gpp": "__ADFGPPSTRING__",
        "gpp_sid": "__ADFGPPSID__",
        "us_privacy": None,
        "auction_price": None,
    },
    "teads": {
        # La table précédente prêtait à Teads une famille [CACHEBUSTER],
        # [CLICK_URL], [CAMPAIGN_ID]… introuvable dans la doc éditeur.
        # Ce que la doc montre est l'inverse : pour intégrer un format Teads
        # via un ad server, on renseigne les macros de l'ad server HÔTE dans
        # les attributs du tag Teads (data-tracking-click="%%CLICK_URL_UNESC%%",
        # data-tracking-start="%%VIEW_URL_UNESC%%" en Google Ad Manager).
        # Teads consomme des macros, il n'en substitue pas une famille propre
        # dans un tag sortant. Rien n'est donc exposé ici.
        "_libelle": "Teads",
        "_statut": "NON_PUBLIC",
        "_source": "https://support.teads.tv/support/solutions/articles/36000066266",
        "_verifie_le": "2026-07-27",
        "_note": "consomme les macros de l'ad server hote (data-tracking-click), n'expose pas de famille propre",
    },
    # ---------------------------------------------------------------- OpenRTB
    "openrtb": {  # OpenRTB 2.6 — la référence quand un SSP dit « macros standard »
        "_libelle": "OpenRTB 2.6 (IAB)",
        "_statut": "NON_VERIFIE",
        "_note": "jeu volontairement reduit : c'est un protocole, pas un ad server",
        "auction_id": "${AUCTION_ID}",
        "auction_price": "${AUCTION_PRICE}",
        "campaign_id": "${AUCTION_AD_ID}",
        "creative_id": "${AUCTION_IMP_ID}",
        "deal_id": "${AUCTION_SEAT_ID}",
        "cachebuster": "${AUCTION_ID}",
        "click_url": None,
        "gdpr": "${GDPR}",
        "gdpr_consent": "${GDPR_CONSENT}",
        "us_privacy": "${US_PRIVACY}",
        "gpp": "${GPP_STRING}",
        "gpp_sid": "${GPP_SID}",
    },
    # ---------------------------------------------------------------- DOOH
    "vistar": {
        "_libelle": "Vistar Media (DOOH)",
        "_statut": "NON_VERIFIE",
        "_note": "issue de la pratique ; centre d'aide Vistar inaccessible en lecture publique",
        "cachebuster": "[CACHEBUSTER]",
        "venue_id": "[VENUE_ID]",
        "screen_id": "[DISPLAY_ID]",
        "lat": "[LATITUDE]",
        "lon": "[LONGITUDE]",
        "plays": "[PLAYS]",
        "impression_multiplier": "[IMPRESSION_MULTIPLIER]",
        "timestamp": "[TIMESTAMP]",
        "click_url": None,        # pas de clic en DOOH
        "ifa": None,              # pas d'identifiant utilisateur : privacy par construction
        "gdpr_consent": None,
    },
    "hivestack": {
        "_libelle": "Hivestack (DOOH)",
        "_statut": "NON_VERIFIE",
        "_note": "issue de la pratique, jamais reconfrontee a la doc editeur",
        "cachebuster": "[CACHE_BUSTER]",
        "venue_id": "[VENUE_ID]",
        "screen_id": "[SCREEN_ID]",
        "lat": "[LAT]",
        "lon": "[LON]",
        "plays": "[PLAY_COUNT]",
        "impression_multiplier": "[IMPRESSIONS]",
        "timestamp": "[TIMESTAMP]",
        "click_url": None,
        "ifa": None,
    },
    "broadsign": {
        # Correction de fond : la table précédente prêtait à Broadsign une
        # famille [VENUE_ID] / [PLAYER_ID] / [PLAYS] qui n'existe pas.
        # Les macros documentées sont celles du Broadsign Control Player
        # (API d'audience temps réel), en DOUBLE ACCOLADE.
        # Conséquences à connaître avant de s'en servir :
        #   - pas de cachebuster, pas de compteur de diffusions, pas de
        #     multiplicateur d'impressions dans cette famille. Le multiplicateur
        #     vit dans le champ dooh.impmultiply de la bid request OpenRTB,
        #     ce qui n'est pas la même surface.
        #   - c'est la surface PLAYER. Broadsign Ads (SSP) est un autre
        #     produit : ne pas transposer ces macros dans un tag SSP.
        "_libelle": "Broadsign Control (player)",
        "_statut": "VERIFIE_DOC",
        "_source": "https://docs.broadsign.com/broadsign-control/latest/en/broadsign-control-real-time-aud-api.html",
        "_verifie_le": "2026-07-27",
        "_note": "famille {{double accolade}}, surface player ; ni cachebuster ni multiplicateur d'impressions",
        "venue_id": "{{location_code}}",
        "screen_id": "{{display_unit_id}}",
        "placement_id": "{{frame_id}}",
        "campaign_id": "{{campaign_id}}",
        "creative_id": "{{content_id}}",
        "lat": "{{latitude}}",
        "lon": "{{longitude}}",
        "timestamp": "{{expected_play_time}}",   # heure d'affichage ATTENDUE, pas constatée
        "app_name": "{{app_name}}",
        "cachebuster": None,
        "plays": None,
        "impression_multiplier": None,
        "click_url": None,
        "ifa": None,
        "gdpr_consent": None,
    },
}

# Rôles pour lesquels la valeur substituée est une URL : elles doivent être
# encodées si elles voyagent DANS un paramètre d'une autre URL. C'est la cause
# n°1 de trafficking cassé — la chaîne de clic insérée non encodée avale tout
# ce qui la suit.
ROLES_URL = {"click_url", "click_url_escaped", "page_url", "referrer", "site_domain"}

# Rôles de consentement : leur absence dans un tag qui cible l'Europe est une
# non-conformité, pas une préférence de trafficking.
ROLES_CONSENT = {"gdpr", "gdpr_consent", "us_privacy", "gpp", "gpp_sid"}

# Rôles inexistants en DOOH : un écran ne connaît personne.
ROLES_ABSENTS_DOOH = {"click_url", "click_url_escaped", "ifa", "ifa_type", "device_id",
                      "user_agent", "gdpr_consent", "us_privacy", "gpp"}

PLATEFORMES_DOOH = ("vistar", "hivestack", "broadsign")

LIBELLES = {k: v["_libelle"] for k, v in PLATEFORMES.items()}
STATUTS = {k: (v.get("_statut", "NON_VERIFIE"), v.get("_note", "")) for k, v in PLATEFORMES.items()}
SOURCES = {k: (v.get("_source"), v.get("_verifie_le")) for k, v in PLATEFORMES.items()}

ORDRE_STATUTS = ("VERIFIE_DOC", "NON_VERIFIE", "NON_PUBLIC")

LEGENDE_STATUTS = {
    "VERIFIE_DOC": "confronte a la doc editeur citee en source",
    "NON_VERIFIE": "issu de la pratique, jamais reconfronte a la doc",
    "NON_PUBLIC": "doc non accessible publiquement : rien n'est expose",
}


def roles(plateforme: str) -> dict[str, str]:
    """Rôles réellement exposés par une plateforme (les None sont écartés)."""
    p = PLATEFORMES[plateforme]
    return {k: v for k, v in p.items() if not k.startswith("_") and v}


def index_inverse(plateforme: str) -> dict[str, str]:
    """syntaxe -> rôle, pour reconnaître les macros d'un tag reçu."""
    return {v: k for k, v in roles(plateforme).items()}


def toutes_les_syntaxes() -> dict[str, list[tuple[str, str]]]:
    """syntaxe -> [(plateforme, rôle)]. Sert à deviner la plateforme d'origine."""
    out: dict[str, list[tuple[str, str]]] = {}
    for plat in PLATEFORMES:
        for role, syn in roles(plat).items():
            out.setdefault(syn, []).append((plat, role))
    return out
