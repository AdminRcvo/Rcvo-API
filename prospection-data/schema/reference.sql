PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
PRAGMA wal_autocheckpoint=10000;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

CREATE TABLE IF NOT EXISTS organizations (
    id INTEGER PRIMARY KEY,
    rcvo_id TEXT NOT NULL UNIQUE,
    legal_name TEXT,
    display_name TEXT,
    normalized_name TEXT,
    website_domain TEXT,
    city TEXT,
    postal_code TEXT,
    country_code TEXT NOT NULL DEFAULT 'FR',
    sector_hint TEXT,
    vo_relevance TEXT NOT NULL DEFAULT 'unknown'
        CHECK(vo_relevance IN ('unknown','possible','likely','confirmed','outside')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','inactive','closed','merged','excluded')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_organizations_name ON organizations(normalized_name);
CREATE INDEX IF NOT EXISTS idx_organizations_domain ON organizations(website_domain);
CREATE INDEX IF NOT EXISTS idx_organizations_city ON organizations(city);

CREATE TABLE IF NOT EXISTS organization_sites (
    id INTEGER PRIMARY KEY,
    rcvo_id TEXT NOT NULL UNIQUE,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    site_name TEXT,
    address_line1 TEXT,
    address_line2 TEXT,
    postal_code TEXT,
    city TEXT,
    country_code TEXT NOT NULL DEFAULT 'FR',
    website_url TEXT,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_sites_org ON organization_sites(organization_id);
CREATE INDEX IF NOT EXISTS idx_sites_city ON organization_sites(city);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    rcvo_id TEXT NOT NULL UNIQUE,
    last_name TEXT,
    first_name TEXT,
    display_name TEXT,
    city TEXT,
    qualification_status TEXT NOT NULL DEFAULT 'to_enrich'
        CHECK(qualification_status IN ('to_enrich','usable','qualified','outside_target','excluded')),
    vo_relevance TEXT NOT NULL DEFAULT 'unknown'
        CHECK(vo_relevance IN ('unknown','possible','likely','confirmed','outside')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','inactive','left_company','merged','excluded')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(last_name, first_name);
CREATE INDEX IF NOT EXISTS idx_contacts_city ON contacts(city);
CREATE INDEX IF NOT EXISTS idx_contacts_qualification
    ON contacts(qualification_status, vo_relevance, status);

CREATE TABLE IF NOT EXISTS contact_employments (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    organization_id INTEGER REFERENCES organizations(id),
    site_id INTEGER REFERENCES organization_sites(id),
    job_title TEXT,
    job_role TEXT,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
    valid_from TEXT,
    valid_to TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_employments_contact ON contact_employments(contact_id, is_current);
CREATE INDEX IF NOT EXISTS idx_employments_org ON contact_employments(organization_id, is_current);

CREATE TABLE IF NOT EXISTS contact_emails (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    email_raw TEXT NOT NULL,
    email_norm TEXT NOT NULL UNIQUE,
    email_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK(email_kind IN ('personal_business','generic_business','unknown')),
    deliverability_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(deliverability_status IN ('unknown','valid','risky','invalid','bounced','unsubscribed')),
    is_primary INTEGER NOT NULL DEFAULT 1 CHECK(is_primary IN (0,1)),
    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    verified_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_contact_emails_contact ON contact_emails(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_emails_status ON contact_emails(deliverability_status);

CREATE TABLE IF NOT EXISTS contact_phones (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    phone_raw TEXT NOT NULL,
    phone_norm TEXT,
    phone_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK(phone_kind IN ('mobile','landline','business','unknown')),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_contact_phones_contact ON contact_phones(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_phones_norm ON contact_phones(phone_norm);

CREATE TABLE IF NOT EXISTS external_identities (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('contact','organization','site')),
    external_id TEXT NOT NULL,
    contact_id INTEGER REFERENCES contacts(id),
    organization_id INTEGER REFERENCES organizations(id),
    site_id INTEGER REFERENCES organization_sites(id),
    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(source_key, entity_type, external_id)
);

CREATE INDEX IF NOT EXISTS idx_external_contact ON external_identities(contact_id);
CREATE INDEX IF NOT EXISTS idx_external_org ON external_identities(organization_id);

CREATE TABLE IF NOT EXISTS source_observations (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL CHECK(target_type IN ('contact','organization','site','email','phone','employment')),
    target_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    observed_value TEXT,
    normalized_value TEXT,
    source_key TEXT NOT NULL,
    raw_batch_uuid TEXT,
    raw_record_id INTEGER,
    observed_at TEXT NOT NULL,
    confidence REAL,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_observations_target
    ON source_observations(target_type, target_id, field_name, observed_at);
CREATE INDEX IF NOT EXISTS idx_observations_source
    ON source_observations(source_key, observed_at);

CREATE TABLE IF NOT EXISTS entity_merges (
    id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('contact','organization','site')),
    source_entity_id INTEGER NOT NULL,
    destination_entity_id INTEGER NOT NULL,
    reason TEXT,
    decided_by TEXT NOT NULL DEFAULT 'sourcing_agent',
    merged_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_merges_destination ON entity_merges(entity_type, destination_entity_id);

CREATE TABLE IF NOT EXISTS data_events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    target_type TEXT,
    target_id INTEGER,
    source_key TEXT,
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_data_events_target ON data_events(target_type, target_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_data_events_type ON data_events(event_type, occurred_at);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    rcvo_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    campaign_kind TEXT NOT NULL DEFAULT 'email',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','scheduled','running','paused','completed','cancelled')),
    starts_at TEXT,
    ends_at TEXT,
    eligibility_snapshot_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS campaign_contacts (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    enrolled_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    status TEXT NOT NULL DEFAULT 'eligible'
        CHECK(status IN ('eligible','queued','sent','responded','suppressed','completed','error')),
    next_eligible_at TEXT,
    last_event_at TEXT,
    UNIQUE(campaign_id, contact_id)
);

CREATE INDEX IF NOT EXISTS idx_campaign_contacts_status
    ON campaign_contacts(campaign_id, status, next_eligible_at);

CREATE TABLE IF NOT EXISTS prospecting_events (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id),
    organization_id INTEGER REFERENCES organizations(id),
    campaign_id INTEGER REFERENCES campaigns(id),
    event_type TEXT NOT NULL
        CHECK(event_type IN (
            'enrolled','queued','email_sent','email_delivered','email_opened','link_clicked',
            'reply_received','followup_scheduled','followup_sent','bounce','optout',
            'campaign_skipped','campaign_completed','manual_note'
        )),
    channel TEXT NOT NULL DEFAULT 'email',
    occurred_at TEXT NOT NULL,
    sender_mailbox TEXT,
    message_key TEXT,
    provider_message_id TEXT,
    result TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_prospecting_contact
    ON prospecting_events(contact_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_prospecting_campaign
    ON prospecting_events(campaign_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_prospecting_type
    ON prospecting_events(event_type, occurred_at);

CREATE TABLE IF NOT EXISTS suppressions (
    id INTEGER PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('contact','email','organization','domain')),
    scope_value TEXT NOT NULL,
    reason TEXT NOT NULL
        CHECK(reason IN ('optout','hard_bounce','client_rcvo','manual_exclusion','legal_hold','invalid_address','other')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    permanent INTEGER NOT NULL DEFAULT 0 CHECK(permanent IN (0,1)),
    starts_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    ends_at TEXT,
    source TEXT,
    notes TEXT,
    UNIQUE(scope_type, scope_value, reason, active)
);

CREATE INDEX IF NOT EXISTS idx_suppressions_active
    ON suppressions(scope_type, scope_value, active);

CREATE TABLE IF NOT EXISTS suppression_events (
    id INTEGER PRIMARY KEY,
    suppression_id INTEGER REFERENCES suppressions(id),
    event_type TEXT NOT NULL CHECK(event_type IN ('added','lifted','expired','corrected')),
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    actor TEXT,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS prospecting_state (
    contact_id INTEGER PRIMARY KEY REFERENCES contacts(id),
    status TEXT NOT NULL DEFAULT 'never_contacted'
        CHECK(status IN ('never_contacted','contacted','followup_possible','followup_scheduled','responded','paused','suppressed','customer')),
    total_emails_sent INTEGER NOT NULL DEFAULT 0,
    first_contact_at TEXT,
    last_contact_at TEXT,
    next_eligible_at TEXT,
    last_campaign_id INTEGER REFERENCES campaigns(id),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE VIEW IF NOT EXISTS v_prospectable_contacts AS
SELECT
    c.id AS contact_id,
    c.rcvo_id AS contact_rcvo_id,
    c.last_name,
    c.first_name,
    c.city,
    ce.email_norm AS email,
    ce.deliverability_status,
    c.qualification_status,
    c.vo_relevance,
    ps.status AS prospecting_status,
    ps.last_contact_at,
    ps.next_eligible_at,
    e.organization_id,
    o.rcvo_id AS organization_rcvo_id,
    o.display_name AS organization_name,
    COALESCE(os.city, o.city) AS organization_city,
    e.job_title,
    e.job_role
FROM contacts c
JOIN contact_emails ce
  ON ce.contact_id = c.id
 AND ce.is_primary = 1
LEFT JOIN prospecting_state ps
  ON ps.contact_id = c.id
LEFT JOIN contact_employments e
  ON e.contact_id = c.id
 AND e.is_current = 1
LEFT JOIN organizations o
  ON o.id = e.organization_id
LEFT JOIN organization_sites os
  ON os.id = e.site_id
WHERE c.status = 'active'
  AND c.qualification_status IN ('usable','qualified')
  AND c.vo_relevance IN ('likely','confirmed')
  AND ce.deliverability_status NOT IN ('invalid','bounced','unsubscribed')
  AND COALESCE(ps.status, 'never_contacted') NOT IN ('suppressed','customer')
  AND (ps.next_eligible_at IS NULL OR ps.next_eligible_at <= strftime('%Y-%m-%dT%H:%M:%fZ','now'))
  AND NOT EXISTS (
      SELECT 1
      FROM suppressions s
      WHERE s.active = 1
        AND (
            (s.scope_type = 'contact' AND s.scope_value = c.rcvo_id)
            OR (s.scope_type = 'email' AND lower(s.scope_value) = ce.email_norm)
            OR (s.scope_type = 'organization' AND o.rcvo_id IS NOT NULL AND s.scope_value = o.rcvo_id)
            OR (s.scope_type = 'domain' AND o.website_domain IS NOT NULL AND lower(s.scope_value) = lower(o.website_domain))
        )
  );

CREATE TRIGGER IF NOT EXISTS trg_no_update_source_observations
BEFORE UPDATE ON source_observations
BEGIN
    SELECT RAISE(ABORT, 'source_observations is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_delete_source_observations
BEFORE DELETE ON source_observations
BEGIN
    SELECT RAISE(ABORT, 'source_observations is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_update_prospecting_events
BEFORE UPDATE ON prospecting_events
BEGIN
    SELECT RAISE(ABORT, 'prospecting_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_delete_prospecting_events
BEFORE DELETE ON prospecting_events
BEGIN
    SELECT RAISE(ABORT, 'prospecting_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_update_suppression_events
BEFORE UPDATE ON suppression_events
BEGIN
    SELECT RAISE(ABORT, 'suppression_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_delete_suppression_events
BEFORE DELETE ON suppression_events
BEGIN
    SELECT RAISE(ABORT, 'suppression_events is append-only');
END;
