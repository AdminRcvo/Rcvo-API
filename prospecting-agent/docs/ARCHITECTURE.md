# Architecture — Rcvo Agent PROSPECTION V1

## Frontière

Entrée et sortie métier unique : API privée de `Rcvo-donnees-references`.

L'Agent ne modifie jamais directement SQLite de la Référence.

## Source de vérité

La Référence décide si un contact est prospectable et conserve :
- campagne ;
- enrôlement ;
- étape courante ;
- envois ;
- relances ;
- réponses ;
- lectures vidéo ;
- bounces ;
- plaintes ;
- oppositions ;
- prochaine date d'éligibilité.

La base locale de l'Agent ne sert qu'à l'exploitation : mailboxes, outbox, feedback reçu mais non encore synchronisé et oppositions locales en attente.

## Séquence

`eligible -> enrolled -> claim -> gate délivrabilité -> outbox prepared -> send -> outbox sent -> Reference event -> outbox synced`

Une séquence terminée impose un délai global de refroidissement avant qu'un contact redevienne disponible pour une autre campagne.

## Feedback

Les fournisseurs disposant de webhooks peuvent appeler `/events/provider`. Les événements sont d'abord persistés localement. Une plainte ou un hard bounce crée immédiatement une suppression locale avant synchronisation.

Un adaptateur IMAP ou une API fournisseur spécifique pourra être ajouté ultérieurement au même contrat sans changer la Référence ni le moteur de campagne.

## Délivrabilité

Les décisions sont explicables et configurables :
- SPF/DKIM/DMARC ;
- TLS obligatoire ;
- fenêtres horaires ;
- cadence minimale ;
- quotas par heure/jour ;
- concentration par domaine destinataire ;
- warm-up journalier ;
- pause sur plainte ;
- pause sur taux de bounce dégradé.

Il n'existe aucun mécanisme de camouflage, de domaine jetable ou de contournement des protections anti-abus.
