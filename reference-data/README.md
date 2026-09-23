# Rcvo-données-références — Candidate V1

Base maître destinée à recevoir **uniquement les données retravaillées par le futur Agent SOURCING**.

Elle est distincte :
- du SaaS Rcvo ;
- de Rcvo Mobile ;
- de la zone tampon d'approvisionnement RAW ;
- du futur moteur d'envoi de l'Agent PROSPECTION.

## Rôle

La zone tampon absorbe vite et sans jugement. L'Agent SOURCING lit RAW, normalise, vérifie, rapproche, enrichit puis appelle cette base de référence.

Flux cible :

`Sources -> Rcvo-approvisionnement-de-donnees (RAW) -> Agent SOURCING -> Rcvo-données-références -> Agent PROSPECTION`

## Ce que la référence garantit

- identifiants Rcvo indépendants des fournisseurs ;
- e-mail normalisé unique, insensible à la casse ;
- entreprise/site/contact séparés ;
- historique des emplois et changements d'entreprise ;
- provenance de chaque observation ;
- identifiants externes Apollo/Hunter/autres conservés sans devenir la clé maître ;
- promotion idempotente d'un enregistrement RAW ;
- rapprochements et fusions historisés ;
- NOM, Prénom, entreprise et ville recherchés mais non bloquants ;
- qualification VO minimale indépendante de la complétude ;
- exclusions et historique de prospection prévus dès le départ ;
- événements sensibles append-only.

## Base physique

La candidate crée `rcvo-reference.sqlite`.

```bash
python3 src/reference_cli.py --db ./data/rcvo-reference.sqlite init
```

## Contrat avec le futur Agent SOURCING

L'agent remet un document normalisé JSON. Exemple :

```json
{
  "raw_ref": {
    "source_key": "apollo",
    "raw_batch_uuid": "batch-123",
    "raw_record_id": 456,
    "source_record_id": "apollo-contact-789"
  },
  "contact": {
    "last_name": "DUPONT",
    "first_name": "Jean",
    "city": "Bordeaux",
    "qualification_status": "usable",
    "vo_relevance": "confirmed",
    "confidence": 0.93
  },
  "email": {
    "value": "jean.dupont@garage-x.fr",
    "kind": "personal_business",
    "deliverability_status": "valid",
    "confidence": 0.98
  },
  "organization": {
    "display_name": "Garage X",
    "domain": "garage-x.fr",
    "city": "Bordeaux",
    "vo_relevance": "confirmed",
    "confidence": 0.95
  },
  "employment": {
    "job_title": "Responsable VO",
    "job_role": "responsable_vo",
    "is_current": true,
    "confidence": 0.9
  }
}
```

Le moteur de référence ne réalise pas de fuzzy matching opaque. Il accepte :
1. les rapprochements déterministes, notamment un même e-mail ou domaine ;
2. un `contact_match_rcvo_id` / `organization_match_rcvo_id` explicitement décidé par l'Agent SOURCING.

Toute décision est historisée.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Important

Cette candidate n'est pas reliée au live. Quand le dépôt dédié sera créé, ce dossier pourra devenir la racine de son propre repo et de son propre environnement AWS.
