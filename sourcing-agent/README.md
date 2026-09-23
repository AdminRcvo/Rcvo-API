# Rcvo Agent SOURCING — Candidate V1

Agent autonome destiné à vivre dans **son propre environnement AWS**, entre :

1. `Rcvo-approvisionnement-de-donnees` — RAW / tampon ;
2. `Rcvo-donnees-references` — base canonique.

Il ne possède pas de base métier parallèle. Sa base locale `sourcing-agent.sqlite` ne conserve que ses runs, décisions, erreurs et métriques.

## Flux

`RAW API -> Agent SOURCING -> Reference API`

Pour chaque élément RAW :

- claim avec lease ;
- identification certaine de la source et du batch ;
- extraction des champs utiles dans des structures hétérogènes ;
- normalisation NOM/Prénom/e-mail/domaine/ville/fonction ;
- qualification du lien automobile / VO ;
- recherche de clés fortes dans la Référence ;
- refus du rapprochement automatique en cas de conflit ;
- création ou enrichissement canonique ;
- ACK RAW uniquement après succès ;
- retry après erreur ;
- journalisation de la décision.

## Philosophie de qualification

L'agent cherche NOM, Prénom, entreprise et ville, mais ne bloque pas une fiche parce qu'un de ces champs manque.

Une fiche devient immédiatement `usable` ou `qualified` lorsqu'elle possède un e-mail exploitable et suffisamment d'indices qu'elle appartient au marché automobile / VO. Sinon elle reste `to_enrich`.

Le moteur n'effectue aucun fuzzy-match silencieux de personnes. Les rapprochements automatiques reposent sur les clés fortes exposées par la Référence : identifiant fournisseur exact, e-mail exact, domaine exact ou ID Rcvo déjà décidé.

## Modes

- `simulation` : normalise, qualifie et journalise sans écrire dans la Référence.
- `production` : promeut réellement les données normalisées.

## Exécution

Variables obligatoires :

```bash
export RCVO_RAW_BASE_URL="https://raw.example.internal"
export RCVO_REFERENCE_BASE_URL="https://reference.example.internal"
export RCVO_RAW_SOURCING_TOKEN="..."
export RCVO_REFERENCE_SOURCING_TOKEN="..."
```

Un lot :

```bash
python3 src/run_agent.py --once
```

Service continu :

```bash
python3 src/service.py
```

Le service expose `/health` et `/ready` pour l'environnement AWS.

## Isolation AWS cible

L'agent doit disposer de :

- son propre compute (ECS/Fargate, EC2 ou équivalent) ;
- son propre stockage persistant pour son journal opérationnel ;
- ses propres logs/alertes ;
- deux secrets séparés : accès RAW et accès Référence ;
- accès réseau privé limité aux deux API concernées ;
- aucun accès à `rcvo.db` ou Rcvo Mobile.

Les jetons Bearer de la candidate servent de contrat simple. En production AWS ils pourront être remplacés par IAM/private networking/mTLS sans changer le moteur de sourcing.
