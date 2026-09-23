# Architecture — Rcvo Agent SOURCING V1

## Composants

### Normalizer
Lit des payloads arbitraires et cherche les champs courants à travers de nombreux alias français/anglais. Il produit un modèle commun indépendant d'Apollo, Hunter ou d'un futur fournisseur.

### Qualification
Évalue uniquement le niveau d'exploitation :
- preuve VO explicite -> `confirmed` ;
- contexte automobile + vente/domaine professionnel -> `likely` ;
- contexte automobile faible -> `possible` ;
- sinon -> `unknown`.

L'incomplétude ne déclenche pas une exclusion.

### Lookup Référence
Avant promotion, l'agent demande à la Référence si des clés fortes correspondent déjà :
- ID fournisseur exact dans la même source ;
- e-mail exact ;
- ID entreprise fournisseur exact ;
- domaine exact.

Si plusieurs clés fortes se contredisent, l'élément est placé en revue/rejet RAW et n'est pas fusionné arbitrairement.

### Promotion
Le document canonique conserve `raw_ref`, la source et le batch, puis transmet contact/e-mail/téléphone/entreprise/fonction. La Référence reste responsable des contraintes finales et de l'idempotence.

### État Agent
La base `sourcing-agent.sqlite` contient seulement :
- runs ;
- décisions ;
- erreurs ;
- versions de profils/règles.

Elle ne devient jamais une troisième base de prospects.

## Résilience

Le lease RAW empêche deux workers de traiter simultanément la même entrée. Un ACK n'est envoyé qu'après succès côté Référence. Les erreurs libèrent la ligne pour retry jusqu'à l'état terminal géré par la zone RAW.

## Déploiement AWS

Les trois environnements restent séparés. Le seul raccordement autorisé pour l'Agent SOURCING est :

- sortie HTTPS vers API RAW ;
- sortie HTTPS vers API Référence ;
- stockage local/volume dédié de son journal ;
- observabilité.

Aucun filesystem partagé entre les bases.
