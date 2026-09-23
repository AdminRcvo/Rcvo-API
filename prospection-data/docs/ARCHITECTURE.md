# Architecture — Rcvo Prospection Data V1

## 1. Frontières

Cette couche n'est ni le SaaS Rcvo, ni Rcvo Mobile.

Elle constitue un système de données autonome qui pourra être déployé sur le même compte AWS, mais avec :
- stockage propre ;
- sauvegardes propres ;
- processus propres ;
- droits IAM propres ;
- aucune dépendance à `rcvo.db`.

## 2. Zone RAW

La zone RAW est volontairement permissive.

### Tables principales
- `raw_sources` : registre des fournisseurs/sources.
- `raw_batches` : un import ou flux identifiable.
- `raw_records` : payload JSON brut, une ligne par élément reçu.
- `raw_batch_events` : début, fin et échec d'un import.

### Choix de performance
- WAL ;
- `synchronous=NORMAL` ;
- insertions `executemany` par lots ;
- peu d'index ;
- aucun contrôle de doublon bloquant à l'entrée ;
- hash de payload optionnel et différable.

Une donnée n'est jamais rejetée uniquement parce qu'elle est pauvre. Le tri vient après la capture.

## 3. Base de référence Rcvo

### Entités
- `organizations` ;
- `organization_sites` ;
- `contacts` ;
- `contact_employments` ;
- `contact_emails` ;
- `contact_phones`.

### Provenance et déduplication
- `external_identities` : IDs Apollo/Hunter/autres, jamais utilisés comme ID maître ;
- `source_observations` : provenance de chaque observation ;
- `entity_merges` : journal des rapprochements/fusions ;
- `data_events` : historique technique/fonctionnel générique.

### Prospection future
- `campaigns` ;
- `campaign_contacts` ;
- `prospecting_events` ;
- `prospecting_state` ;
- `suppressions` ;
- `suppression_events`.

L'historique de prospection est présent avant même l'Agent PROSPECTION afin d'éviter une migration structurante au moment où les premiers envois commenceront.

## 4. Contact incomplet mais exploitable

La complétude n'est pas une condition de stockage.

Le NOM, le Prénom, l'entreprise et la ville sont des données recherchées et valorisées, mais non obligatoires. Un contact peut être exploitable dès lors que l'Agent SOURCING a suffisamment confiance dans son rattachement au marché VO et qu'un email utilisable est connu.

La vue `v_prospectable_contacts` matérialise cette règle sans supprimer les contacts encore à enrichir.

## 5. Historique

Trois familles sont append-only :
- observations de source ;
- événements de prospection ;
- événements d'exclusion.

Une correction s'effectue par un nouvel événement ou une nouvelle observation, jamais par réécriture silencieuse du passé.

Cela permet de répondre plus tard à :
- Quand ce contact a-t-il été découvert ?
- Par quelle source ?
- Quand lui avons-nous écrit ?
- Dans quelle campagne ?
- Combien de fois ?
- A-t-il répondu ou refusé ?
- À partir de quand peut-il être réévalué pour une nouvelle campagne ?

## 6. AWS cible

Pour SQLite, la topologie recommandée est **un seul writer** sur un stockage bloc persistant, avec sauvegardes/snapshots. Les futurs agents ne devront pas monter chacun le même fichier SQLite via un partage réseau et écrire librement en concurrence.

Si le besoin devient multi-writer, la frontière logique restera la même mais le moteur de la base de référence pourra migrer vers PostgreSQL.

La zone RAW et la référence peuvent avoir des politiques de rétention/sauvegarde distinctes.
