# Rcvo Prospection Data — Foundation V2

Fondation de données **strictement isolée** du SaaS Rcvo et de Rcvo Mobile.

Objectif : fournir un réservoir d'approvisionnement très large pour le futur Agent SOURCING puis, plus tard, pour l'Agent PROSPECTION.

## Architecture

Deux bases SQLite physiques distinctes :

- `raw.sqlite` : zone tampon RAW à haut débit. Elle absorbe d'abord, trie ensuite.
- `reference.sqlite` : base de référence Rcvo, normalisée, dédupliquée et historisée.

Pipeline cible :

`Fournisseurs / fichiers / API / webhooks -> RAW -> Agent SOURCING -> Référence Rcvo -> Agent PROSPECTION`

Aucune dépendance à `rcvo.db`. Aucun déploiement vers le SaaS Rcvo.

## Ce que la zone RAW accepte

Formats intégrés : CSV, CSV point-virgule, TSV, PSV, JSON, JSONL/NDJSON, XML, XLSX, XLS, tables HTML, ZIP, GZIP, BZ2 et XZ/LZMA. Parquet est prévu via l'adaptateur optionnel `pyarrow`.

Les fichiers peuvent être importés individuellement ou par répertoire entier. Les gros tableaux JSON sont streamés avec `ijson` afin d'éviter de charger tout l'export en mémoire.

Les API HTTP(S) peuvent être GET ou POST et utiliser clé API/Bearer par variable d'environnement, Basic Auth ou OAuth2 Client Credentials. La pagination intégrée couvre page, offset, cursor et next-URL. Les réponses 429/5xx sont retentées avec backoff et les limites de débit fournisseur peuvent être configurées.

Un récepteur webhook minimal est également fourni pour les fournisseurs capables de pousser leurs données.

## Principe de conservation

Avant parsing, chaque fichier ou réponse fournisseur est archivé dans un stockage de contenu adressé par SHA-256 sous `data/artifacts/sha256/`. Deux imports strictement identiques provenant de la même source sont reconnus et ne sont pas réinjectés par défaut.

Les erreurs de parsing ne stoppent pas un import massif : la ligne ou le fragment problématique part dans `raw_ingest_failures`, tandis que les autres enregistrements continuent d'être absorbés.

Les doublons métier restent volontairement autorisés dans RAW. Leur rapprochement appartient au futur Agent SOURCING.

## Initialisation

```bash
python3 -m pip install -r requirements-ingestion.txt
python3 src/rcvo_ingest.py --data-dir ./data init
```

## Import de fichiers

```bash
python3 src/rcvo_ingest.py --data-dir ./data file \
  --source apollo-export-2026-10 \
  --source-name Apollo \
  --file /chemin/export.csv
```

Un répertoire complet :

```bash
python3 src/rcvo_ingest.py --data-dir ./data dir \
  --source import-massif-2026-10 \
  --directory /chemin/exports
```

## Connexion générique à une API fournisseur

Copier et adapter l'un des manifests de `connectors/examples/`, puis conserver les secrets uniquement dans des variables d'environnement.

```bash
python3 src/rcvo_ingest.py --data-dir ./data http \
  --config connectors/examples/http-page.json
```

Les checkpoints sont persistés après chaque page. Une interruption permet donc de reprendre sans repartir de zéro. `--restart` force un redémarrage volontaire du parcours.

## Webhook

Le receiver n'est pas destiné à être exposé directement sur Internet. Sur AWS, il devra être placé derrière la passerelle/ALB/API Gateway prévue, avec TLS et contrôle réseau.

```bash
export RCVO_RAW_INGEST_TOKEN='...'
python3 scripts/webhook_receiver.py --data-dir ./data
```

## File de travail du futur Agent SOURCING

`raw_queue.py` fournit une file avec baux : claim par paquets, expiration automatique, reprise après crash, compteur de tentatives, ACK, retry, rejet terminal. Plusieurs lecteurs peuvent donc travailler sans perdre les éléments ; SQLite conserve un writer coordonné.

## Performance et robustesse

- WAL, `synchronous=NORMAL`, cache mémoire et mmap ;
- transactions par lots de 25 000 lignes par défaut ;
- très peu d'index sur le chemin critique RAW ;
- artefacts bruts conservés avant parsing ;
- idempotence par SHA-256 ;
- checkpoints API ;
- dead-letter pour les erreurs ;
- métriques de débit par batch ;
- maintenance `quick_check/integrity_check`, `ANALYZE`, `PRAGMA optimize` et checkpoint WAL.

Benchmark :

```bash
python3 scripts/benchmark_ingest.py --rows 250000
```

Maintenance :

```bash
python3 scripts/raw_maintenance.py --data-dir ./data
```

## Base de référence Rcvo

La référence accepte les fiches incomplètes : NOM, Prénom, entreprise et ville sont recherchés, mais ne bloquent pas le stockage. La vue `v_prospectable_contacts` exige seulement la qualification minimale définie, un e-mail exploitable et aucune exclusion.

Les observations de source, événements de prospection et exclusions sont historisés. Les identifiants Apollo, Hunter ou autres restent secondaires par rapport aux identifiants Rcvo.

## Extensibilité

Le format interne RAW est volontairement fournisseur-agnostique : un nouveau connecteur transforme simplement son entrée en enregistrements RAW sans modifier la base. Un fournisseur propriétaire nécessitant un SDK ou protocole spécifique pourra donc recevoir un adaptateur isolé sans toucher au schéma central ni au futur Agent SOURCING.

Les imports doivent utiliser les API, exports, fichiers ou mécanismes autorisés par chaque fournisseur et respecter les droits de conservation associés.
