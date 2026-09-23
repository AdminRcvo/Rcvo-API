# Architecture — Rcvo Prospection Data V2

## Frontière stricte

Ce composant est autonome : stockage, code, processus, sauvegardes et futurs droits IAM propres. Il ne lit ni n'écrit la base du SaaS Rcvo.

## RAW : capture avant intelligence

La priorité de RAW est de conserver vite et fidèlement ce qui a été acquis. Les contrôles métier et la déduplication contact/entreprise ne sont pas effectués sur le chemin critique d'ingestion.

Chaque entrée est rattachée à une source et un batch. Les fichiers/réponses originaux sont archivés par hash avant parsing. Les lignes valides deviennent `raw_records`; les éléments illisibles sont conservés dans `raw_ingest_failures`.

Les tables supplémentaires V2 couvrent :
- configurations de connecteurs sans secret en clair ;
- artefacts bruts et liaison artefact/batch ;
- runs de collecte réseau ;
- checkpoints de pagination/reprise ;
- métriques d'ingestion ;
- leases et compteur de tentatives pour les workers du futur Agent SOURCING ;
- maintenance et contrôles d'intégrité.

## Connecteurs

Le connecteur HTTP est déclaratif. Un manifest décrit transport, auth, pagination, réponse et débit. Les secrets sont fournis par variables d'environnement.

Auth supportée : Bearer/API-key header, API-key query, Basic, OAuth2 Client Credentials.

Pagination supportée : numéro de page, offset/limit, cursor, URL suivante.

Formats supportés par le parseur commun : CSV/TSV/PSV, JSON/JSONL, XML, XLSX/XLS, HTML table, archives/compressions courantes, Parquet optionnel.

Le receiver webhook réutilise exactement le même pipeline RAW. Un adaptateur fournisseur spécifique pourra réutiliser `ingest_bytes` ou `ingest_parsed_records` sans modifier le schéma.

## Idempotence et provenance

RAW n'empêche pas les doublons métier : deux fournisseurs peuvent légitimement fournir le même contact et le futur Agent SOURCING doit pouvoir comparer leurs observations.

En revanche, le même artefact binaire provenant de la même source possède un SHA-256 unique. Cela évite de retraiter accidentellement le même export plusieurs fois, tout en autorisant un retraitement explicite avec `--force`.

## File de traitement

Le futur Agent SOURCING ne lira pas RAW avec un simple SELECT non coordonné. Il utilisera les leases :
1. claim atomique d'un paquet ;
2. bail expirant automatiquement ;
3. ACK après promotion/traitement ;
4. retry après erreur ;
5. état terminal après un nombre maximal de tentatives.

Une panne du worker ne perd donc pas les données.

## Référence Rcvo

La base de référence reste séparée physiquement. Les entités Rcvo possèdent leurs propres IDs et conservent les identifiants fournisseurs comme références externes.

NOM, Prénom, entreprise et ville sont valorisés mais non bloquants. La qualification minimale pour la prospection reste indépendante de la complétude de la fiche.

## Historique de prospection

Campagnes, envois, relances, réponses, bounces, opt-out et dates de prochaine éligibilité ont leur structure dédiée. Les événements sensibles sont append-only.

## AWS cible

Avec SQLite, le déploiement doit conserver un writer coordonné sur stockage bloc persistant. Les artefacts peuvent ensuite être externalisés vers S3 sans changer leur modèle logique.

Si plusieurs agents doivent écrire réellement en parallèle à grande échelle, la frontière de données permet une migration de la référence vers PostgreSQL. Le pipeline d'ingestion, les IDs Rcvo et les événements restent conceptuellement identiques.
