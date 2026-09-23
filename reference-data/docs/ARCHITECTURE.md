# Architecture — Rcvo-données-références V1

## Principe

La référence ne stocke pas le vrac. Elle stocke l'état canonique du marché prospectable tel qu'il a été interprété par le futur Agent SOURCING, tout en conservant les preuves et observations qui ont conduit à cet état.

## Séparation des responsabilités

### Zone RAW
Capture et conservation brute, rapide, permissive.

### Agent SOURCING
Normalisation, détection des doublons, résolution d'identité, enrichissement et qualification VO.

### Base de référence
Persistance canonique, contraintes d'intégrité, historique et état exploitable.

### Agent PROSPECTION
Lecture des contacts éligibles, campagnes et écriture des événements d'envoi/réponse/exclusion.

## Résolution d'identité

La base applique automatiquement uniquement des clés fortes :
- e-mail normalisé exact ;
- identifiant externe exact pour une source ;
- domaine d'entreprise exact ;
- identifiant Rcvo explicitement fourni.

Les décisions plus faibles (nom + entreprise, fonction, ville, téléphone partagé, etc.) appartiennent à l'Agent SOURCING et sont consignées dans `match_decisions`.

## Modèle entreprise

`organizations` représente l'entité commerciale/groupe pertinent.
`organization_sites` représente un établissement/concession.
`organization_domains` permet plusieurs domaines par entreprise.
`organization_aliases` conserve les variantes de nom.

## Modèle contact

`contacts` porte l'identité canonique.
`contact_emails` et `contact_phones` sont séparés pour historiser qualité et provenance.
`contact_employments` conserve les fonctions présentes et passées sans écraser l'historique.

## Promotion RAW -> référence

`promotion_receipts` est une barrière d'idempotence. Un triplet source/batch/raw_record ne peut être promu qu'une fois.

`promotion_events` décrit ce qui a été créé, retrouvé ou enrichi.

`source_observations` est append-only et garde l'origine de chaque valeur.

## Prospection

La référence contient la mémoire nécessaire à l'Agent PROSPECTION :
- campagnes ;
- état courant ;
- événements d'envoi ;
- relances ;
- réponses ;
- bounces ;
- opt-out ;
- suppressions.

La vue `v_prospectable_contacts` fournit uniquement les contacts actuellement exploitables.

## Portabilité

SQLite est adapté au démarrage et à un writer coordonné. Les clés, contraintes et événements sont volontairement simples afin de rendre possible une migration future vers PostgreSQL sans réinventer le modèle.
