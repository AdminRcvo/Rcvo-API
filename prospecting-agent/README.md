# Rcvo Agent PROSPECTION — Candidate V1

Quatrième brique autonome de la chaîne Rcvo. Cet agent est relié **uniquement** à `Rcvo-donnees-references`.

Il ne cherche pas de prospects et ne nettoie pas les données. Il orchestre la prospection à partir des contacts que la Référence déclare éligibles.

## Responsabilités

- créer/synchroniser les campagnes et versions de messages ;
- enrôler massivement les contacts actuellement prospectables ;
- réclamer des lots avec lease pour éviter les doubles envois ;
- répartir les envois entre boîtes saines selon leur capacité ;
- respecter fenêtres horaires, plafonds horaires/journaliers, plafond par domaine destinataire et montée en charge ;
- envoyer via SMTP chiffré ou futur adaptateur fournisseur ;
- ajouter automatiquement identité de l'expéditeur, lien de désinscription visible et en-têtes one-click ;
- conserver une outbox locale transactionnelle pour empêcher un renvoi après succès SMTP + panne de la Référence ;
- synchroniser l'historique complet dans la Référence ;
- gérer relances, réponses, hard/soft bounces, plaintes spam et exclusions ;
- surveiller les boîtes en IMAP lorsqu'il est activé afin de détecter les réponses et DSN même sans webhook fournisseur ;
- exposer une landing page vidéo Rcvo et enregistrer les lectures volontaires ;
- suspendre automatiquement une boîte lorsqu'un signal de santé devient mauvais.

## Délivrabilité

La candidate recherche la meilleure délivrabilité possible, mais aucun système ne peut garantir qu'un message n'ira jamais en spam.

En Production SMTP, le domaine d'une boîte est refusé si le pré-contrôle ne trouve pas SPF + DKIM + DMARC. TLS est obligatoire.

Les plafonds fournis dans `config/mailboxes.json` sont des **valeurs de départ conservatrices**, pas des seuils magiques pour contourner les filtres. Ils doivent être ajustés à la réputation réelle des domaines, aux limites du fournisseur et aux métriques observées.

Une plainte spam peut mettre immédiatement la boîte en pause. Les hard bounces et soft bounces sont surveillés sur une fenêtre glissante et peuvent également déclencher une pause.

## Anti-double-envoi

Chaque contact/campagne/étape possède une ligne unique dans l'outbox locale.

Ordre :
1. préparation locale ;
2. envoi ;
3. marquage local `sent` ;
4. synchronisation vers la Référence ;
5. marquage local `synced`.

Si l'étape 4 échoue, le mail reste `sent` et sera uniquement resynchronisé : **il n'est pas renvoyé**.

Un timeout SMTP peut être intrinsèquement ambigu. Dans ce cas, la candidate classe l'envoi `uncertain` et bloque sa répétition automatique plutôt que de risquer un doublon.

## Désinscription

Chaque message contient :
- un lien visible de désinscription ;
- `List-Unsubscribe` ;
- `List-Unsubscribe-Post: List-Unsubscribe=One-Click`.

L'opposition est d'abord enregistrée localement. Même si la Référence est momentanément indisponible, l'Agent conserve l'opposition et la synchronise ensuite avant tout nouvel envoi.

## Vidéo

La vidéo n'est pas jointe au courriel. Le message pointe vers une landing page Rcvo. Pour une vidéo hébergée directement (MP4/WebM), l'événement est enregistré au premier `play`. Pour un lien externe, l'événement est enregistré lorsque le prospect clique volontairement pour ouvrir la vidéo.

Aucun pixel invisible n'est nécessaire.

## AWS cible

Environnement séparé avec :
- image Docker propre ;
- stockage persistant de la petite outbox opérationnelle ;
- secrets SMTP / API dans AWS Secrets Manager ;
- accès HTTPS privé à la Référence ;
- endpoint HTTPS public limité aux désinscriptions, vidéo et webhooks fournisseur ;
- logs, métriques et alarmes ;
- aucun accès à RAW, à l'Agent SOURCING, à Rcvo SaaS ou Rcvo Mobile.

## Avant Production

Le fichier d'exemple contient volontairement des valeurs à remplacer : domaine SMTP réel, sélecteur DKIM, adresse postale/identité complète, URL publique de l'Agent et URL finale de la vidéo.

La première activation doit se faire en petits volumes, puis augmenter uniquement si les indicateurs de délivrabilité restent sains.


## Capacité d'envoi candidate

Le chiffre de 25 messages/jour n'est pas un plafond Rcvo. La candidate utilise
désormais une montée en charge progressive par boîte jusqu'à **200 messages/jour**
dans la configuration d'exemple, uniquement si la boîte reste saine.

La rampe d'exemple est :
`10 -> 15 -> 25 -> 40 -> 60 -> 80 -> 100 -> 120 -> 140 -> 160 -> 180 -> 200`.

Ce plafond est un paramètre Rcvo, pas une garantie de délivrabilité ni une limite
imposée par Gmail/Yahoo/Outlook. Les garde-fous réputationnels peuvent arrêter
ou ralentir la boîte avant ce niveau. Le volume global augmente en ajoutant des
boîtes authentifiées et saines, sans augmenter artificiellement la pression sur
un même domaine destinataire.
