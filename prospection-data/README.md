# Rcvo Prospection Data — Foundation V1

Fondation de données **strictement isolée** du SaaS Rcvo et de Rcvo Mobile.

Objectif : fournir le réservoir de données du futur Agent SOURCING puis, plus tard, de l'Agent PROSPECTION.

## Principe

Deux bases SQLite physiques distinctes :

- `raw.sqlite` : **zone tampon RAW**, optimisée pour absorber rapidement des exports/API/CSV/JSONL hétérogènes sans exiger un schéma fournisseur commun et sans bloquer les doublons.
- `reference.sqlite` : **base de référence Rcvo**, normalisée, dédupliquée et historisée.

Le pipeline cible est :

`Sources -> RAW -> Agent SOURCING -> Référence Rcvo -> Agent PROSPECTION`

La couche RAW privilégie la capture : elle accepte les données incomplètes et arbitraires. Le futur Agent SOURCING aura la responsabilité de normaliser, rapprocher, dédupliquer, enrichir et qualifier avant promotion dans la référence.

## Règles métier déjà encodées

- Une fiche de référence peut exister sans NOM, Prénom, entreprise ou ville.
- Pour être exposé comme prospectable, un contact doit au minimum avoir :
  - un email,
  - une qualification `usable` ou `qualified`,
  - une pertinence VO `likely` ou `confirmed`,
  - aucune exclusion active.
- Les coordonnées et identités fournisseurs restent secondaires : les entités Rcvo possèdent leur propre `rcvo_id`.
- Un même email normalisé ne peut pas devenir deux destinations de prospection différentes.
- Les observations de source, événements de prospection et événements d'exclusion sont **append-only**.
- Une opposition, un hard bounce, un client Rcvo ou une exclusion manuelle peut neutraliser une destination sans effacer son historique.
- L'historique de campagne est prévu dès la V1 afin de savoir quand un mail a été envoyé et quand un contact redevient éligible.

## Initialisation

Aucune dépendance Python externe n'est nécessaire.

```bash
python3 src/rcvo_data.py --data-dir ./data init
```

Les fichiers SQLite sont créés sous `./data/` et sont ignorés par Git.

## Ingestion rapide

CSV arbitraire :

```bash
python3 src/rcvo_data.py --data-dir ./data ingest-csv \
  --source apollo-2026-10 \
  --source-name Apollo \
  --source-kind provider_export \
  --file /chemin/export.csv
```

JSONL :

```bash
python3 src/rcvo_data.py --data-dir ./data ingest-jsonl \
  --source source-x-2026-10 \
  --file /chemin/export.jsonl
```

L'ingestion est effectuée en transactions par lots (10 000 lignes par défaut). Le hash du payload RAW est volontairement facultatif (`--hash`) afin de ne pas ralentir la phase de capture.

## Statistiques

```bash
python3 src/rcvo_data.py --data-dir ./data stats
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Benchmark local

```bash
python3 scripts/benchmark_ingest.py --rows 100000
```

Le chiffre obtenu dépend fortement de la machine et du stockage. Ce benchmark mesure la mécanique d'ingestion, pas la vitesse d'un fournisseur externe.

## Isolation

Cette fondation ne lit ni n'écrit `rcvo.db`, n'importe aucun module du SaaS Rcvo et ne contient aucun workflow de déploiement vers le live. Elle est destinée à un environnement AWS séparé.

SQLite est conservé pour la V1 avec WAL et transactions batch. Le schéma évite les dépendances SQLite exotiques afin de garder une migration future vers PostgreSQL réaliste si plusieurs agents doivent écrire concurremment ou si la volumétrie l'exige.

## Sources et conservation

`raw_sources` et `raw_batches` permettent de conserver la provenance, le mode d'acquisition et un instantané contractuel/métadonnée utile. Les imports doivent utiliser les mécanismes (API, exports, fichiers) autorisés par chaque source et les droits de conservation applicables.
