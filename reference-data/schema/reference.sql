PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=10000;
PRAGMA wal_autocheckpoint=10000;
PRAGMA cache_size=-65536;
PRAGMA mmap_size=268435456;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

INSERT INTO schema_meta(key,value) VALUES ('schema_version','1')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

CREATE TABLE IF NOT EXISTS organizations (
    id INTEGER PRIMARY KEY,
    rcvo_id TEXT NOT NULL UNIQUE,
    legal_name TEXT,
    display_name TEXT,
    normalized_name TEXT,
    registration_number TEXT,
    city TEXT,
    postal_code TEXT,
    country_code TEXT NOT NULL DEFAULT 'FR',
    sector_hint TEXT,
    vo_relevance TEXT NOT NULL DEFAULT 'unknown'
        CHECK(vo_relevance IN ('unknown','possible','likely','confirmed','outside')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','inactive','closed','merged','excluded')),
    confidence REAL NOT NULL DEFAULT 0.0 CHECK(confidence >= 0 AND confidence <= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_org_name_city
    ON organizations(normalized_name, city);
CREATE INDEX IF NOT EXISTS idx_org_registration
    ON organizations(registration_number);
CREATE INDEX IF NOT EXISTS idx_org_relevance
    ON organizations(vo_relevance, status);

CREATE TABLE IF NOT EXISTS organization_aliases (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    alias_raw TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    source_key TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(organization_id, alias_norm)
);
CREATE INDEX IF NOT EXISTS idx_org_alias_norm ON organization_aliases(alias_norm);

CREATE TABLE IF NOT EXISTS organization_domains (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    domain_raw TEXT NOT NULL,
    domain_norm TEXT NOT NULL COLLATE NOCASE UNIQUE,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_domains_org ON organization_domains(organization_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_org_primary_domain
    ON organization_domains(organization_id)
    WHERE is_primary=1;

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
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','inactive','closed','merged')),
    confidence REAL NOT NULL DEFAULT 0.0 CHECK(confidence >= 0 AND confidence <= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sites_org ON organization_sites(organization_id);
CREATE INDEX IF NOT EXISTS idx_sites_city ON organization_sites(city,postal_code);

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
    confidence REAL NOT NULL DEFAULT 0.0 CHECK(confidence >= 0 AND confidence <= 1),
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_name
    ON contacts(last_name,first_name);
CREATE INDEX IF NOT EXISTS idx_contacts_city ON contacts(city);
CREATE INDEX IF NOT EXISTS idx_contacts_target
    ON contacts(status,qualification_status,vo_relevance);

CREATE TABLE IF NOT EXISTS contact_emails (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    email_raw TEXT NOT NULL,
    email_norm TEXT NOT NULL COLLATE NOCASE UNIQUE,
    email_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK(email_kind IN ('personal_business','generic_business','unknown')),
    deliverability_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(deliverability_status IN ('unknown','valid','risky','invalid','bounced','unsubscribed')),
    ownership_confidence REAL NOT NULL DEFAULT 0.0
        CHECK(ownership_confidence >= 0 AND ownership_confidence <= 1),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    verified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_contact_emails_contact
    ON contact_emails(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_emails_quality
    ON contact_emails(deliverability_status,email_kind);
CREATE UNIQUE INDEX IF NOT EXISTS uq_contact_primary_email
    ON contact_emails(contact_id)
    WHERE is_primary=1;

CREATE TABLE IF NOT EXISTS contact_phones (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    phone_raw TEXT NOT NULL,
    phone_norm TEXT,
    phone_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK(phone_kind IN ('mobile','landline','business','unknown')),
    ownership_confidence REAL NOT NULL DEFAULT 0.0
        CHECK(ownership_confidence >= 0 AND ownership_confidence <= 1),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contact_phones_contact
    ON contact_phones(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_phones_norm
    ON contact_phones(phone_norm);
CREATE UNIQUE INDEX IF NOT EXISTS uq_contact_primary_phone
    ON contact_phones(contact_id)
    WHERE is_primary=1;

CREATE TABLE IF NOT EXISTS contact_employments (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    organization_id INTEGER REFERENCES organizations(id),
    site_id INTEGER REFERENCES organization_sites(id),
    job_title TEXT,
    job_title_norm TEXT,
    job_role TEXT,
    department TEXT,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
    confidence REAL NOT NULL DEFAULT 0.0 CHECK(confidence >= 0 AND confidence <= 1),
    valid_from TEXT,
    valid_to TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_employments_contact
    ON contact_employments(contact_id,is_current);
CREATE INDEX IF NOT EXISTS idx_employments_org
    ON contact_employments(organization_id,is_current);
CREATE INDEX IF NOT EXISTS idx_employments_role
    ON contact_employments(job_role,is_current);

CREATE TABLE IF NOT EXISTS external_identities (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    entity_type TEXT NOT NULL
        CHECK(entity_type IN ('contact','organization','site','email','employment')),
    external_id TEXT NOT NULL,
    contact_id INTEGER REFERENCES contacts(id),
    organization_id INTEGER REFERENCES organizations(id),
    site_id INTEGER REFERENCES organization_sites(id),
    email_id INTEGER REFERENCES contact_emails(id),
    employment_id INTEGER REFERENCES contact_employments(id),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(source_key,entity_type,external_id)
);
CREATE INDEX IF NOT EXISTS idx_external_contact ON external_identities(contact_id);
CREATE INDEX IF NOT EXISTS idx_external_org ON external_identities(organization_id);

CREATE TABLE IF NOT EXISTS promotion_receipts (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    raw_batch_uuid TEXT NOT NULL,
    raw_record_id INTEGER NOT NULL,
    source_record_id TEXT,
    promoted_at TEXT NOT NULL,
    result_status TEXT NOT NULL
        CHECK(result_status IN ('created','enriched','matched','ignored','rejected')),
    contact_id INTEGER REFERENCES contacts(id),
    organization_id INTEGER REFERENCES organizations(id),
    details_json TEXT,
    UNIQUE(source_key,raw_batch_uuid,raw_record_id)
);
CREATE INDEX IF NOT EXISTS idx_promotion_receipts_contact
    ON promotion_receipts(contact_id,promoted_at);
CREATE INDEX IF NOT EXISTS idx_promotion_receipts_org
    ON promotion_receipts(organization_id,promoted_at);

CREATE TABLE IF NOT EXISTS promotion_events (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    raw_batch_uuid TEXT,
    raw_record_id INTEGER,
    event_type TEXT NOT NULL
        CHECK(event_type IN (
            'promotion_started','contact_created','contact_matched','contact_enriched',
            'organization_created','organization_matched','organization_enriched',
            'email_added','email_reused','employment_added','employment_closed',
            'external_identity_linked','promotion_completed','promotion_rejected'
        )),
    occurred_at TEXT NOT NULL,
    contact_id INTEGER REFERENCES contacts(id),
    organization_id INTEGER REFERENCES organizations(id),
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_promotion_events_raw
    ON promotion_events(source_key,raw_batch_uuid,raw_record_id,occurred_at);
CREATE INDEX IF NOT EXISTS idx_promotion_events_contact
    ON promotion_events(contact_id,occurred_at);

CREATE TABLE IF NOT EXISTS source_observations (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL
        CHECK(target_type IN ('contact','organization','site','email','phone','employment')),
    target_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    observed_value TEXT,
    normalized_value TEXT,
    source_key TEXT NOT NULL,
    raw_batch_uuid TEXT,
    raw_record_id INTEGER,
    observed_at TEXT NOT NULL,
    confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_observations_target
    ON source_observations(target_type,target_id,field_name,observed_at);
CREATE INDEX IF NOT EXISTS idx_observations_raw
    ON source_observations(source_key,raw_batch_uuid,raw_record_id);

CREATE TABLE IF NOT EXISTS match_decisions (
    id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('contact','organization','site')),
    incoming_key TEXT,
    candidate_rcvo_id TEXT,
    decision TEXT NOT NULL CHECK(decision IN ('matched','new','rejected','needs_review')),
    method TEXT NOT NULL,
    score REAL CHECK(score IS NULL OR (score >= 0 AND score <= 1)),
    decided_by TEXT NOT NULL DEFAULT 'sourcing_agent',
    reason TEXT,
    source_key TEXT,
    raw_batch_uuid TEXT,
    raw_record_id INTEGER,
    decided_at TEXT NOT NULL,
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_match_decisions_raw
    ON match_decisions(source_key,raw_batch_uuid,raw_record_id);
CREATE INDEX IF NOT EXISTS idx_match_decisions_candidate
    ON match_decisions(candidate_rcvo_id,decided_at);

CREATE TABLE IF NOT EXISTS entity_merges (
    id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('contact','organization','site')),
    source_rcvo_id TEXT NOT NULL,
    destination_rcvo_id TEXT NOT NULL,
    reason TEXT,
    decided_by TEXT NOT NULL DEFAULT 'sourcing_agent',
    merged_at TEXT NOT NULL,
    metadata_json TEXT,
    CHECK(source_rcvo_id <> destination_rcvo_id)
);
CREATE INDEX IF NOT EXISTS idx_merges_source
    ON entity_merges(entity_type,source_rcvo_id);
CREATE INDEX IF NOT EXISTS idx_merges_destination
    ON entity_merges(entity_type,destination_rcvo_id);

CREATE TABLE IF NOT EXISTS data_events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    target_type TEXT,
    target_rcvo_id TEXT,
    source_key TEXT,
    occurred_at TEXT NOT NULL,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_data_events_target
    ON data_events(target_type,target_rcvo_id,occurred_at);
CREATE INDEX IF NOT EXISTS idx_data_events_type
    ON data_events(event_type,occurred_at);

CREATE TABLE IF NOT EXISTS suppressions (
    id INTEGER PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('contact','email','organization','domain')),
    scope_value TEXT NOT NULL COLLATE NOCASE,
    reason TEXT NOT NULL
        CHECK(reason IN ('optout','hard_bounce','client_rcvo','manual_exclusion','legal_hold','invalid_address','other')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    permanent INTEGER NOT NULL DEFAULT 0 CHECK(permanent IN (0,1)),
    starts_at TEXT NOT NULL,
    ends_at TEXT,
    source TEXT,
    notes TEXT,
    UNIQUE(scope_type,scope_value,reason)
);
CREATE INDEX IF NOT EXISTS idx_suppressions_active
    ON suppressions(scope_type,scope_value,active);

CREATE TABLE IF NOT EXISTS suppression_events (
    id INTEGER PRIMARY KEY,
    suppression_id INTEGER REFERENCES suppressions(id),
    event_type TEXT NOT NULL CHECK(event_type IN ('added','lifted','expired','corrected')),
    occurred_at TEXT NOT NULL,
    actor TEXT,
    details_json TEXT
);

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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_contacts (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    enrolled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'eligible'
        CHECK(status IN ('eligible','queued','sent','responded','suppressed','completed','error')),
    next_eligible_at TEXT,
    last_event_at TEXT,
    UNIQUE(campaign_id,contact_id)
);
CREATE INDEX IF NOT EXISTS idx_campaign_contacts_status
    ON campaign_contacts(campaign_id,status,next_eligible_at);

CREATE TABLE IF NOT EXISTS prospecting_events (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id),
    organization_id INTEGER REFERENCES organizations(id),
    campaign_id INTEGER REFERENCES campaigns(id),
    event_type TEXT NOT NULL
        CHECK(event_type IN (
            'enrolled','queued','email_sent','email_delivered','email_opened',
            'link_clicked','reply_received','followup_scheduled','followup_sent',
            'bounce','optout','campaign_skipped','campaign_completed','manual_note'
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
    ON prospecting_events(contact_id,occurred_at);
CREATE INDEX IF NOT EXISTS idx_prospecting_campaign
    ON prospecting_events(campaign_id,occurred_at);

CREATE TABLE IF NOT EXISTS prospecting_state (
    contact_id INTEGER PRIMARY KEY REFERENCES contacts(id),
    status TEXT NOT NULL DEFAULT 'never_contacted'
        CHECK(status IN (
            'never_contacted','contacted','followup_possible','followup_scheduled',
            'responded','paused','suppressed','customer'
        )),
    total_emails_sent INTEGER NOT NULL DEFAULT 0,
    first_contact_at TEXT,
    last_contact_at TEXT,
    next_eligible_at TEXT,
    last_campaign_id INTEGER REFERENCES campaigns(id),
    updated_at TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS v_contact_master AS
SELECT
    c.id AS contact_id,
    c.rcvo_id,
    c.last_name,
    c.first_name,
    c.display_name,
    c.city AS contact_city,
    c.qualification_status,
    c.vo_relevance,
    c.status,
    c.confidence,
    ce.email_norm AS primary_email,
    ce.deliverability_status AS email_status,
    ce.email_kind,
    cp.phone_norm AS primary_phone,
    e.job_title,
    e.job_role,
    o.id AS organization_id,
    o.rcvo_id AS organization_rcvo_id,
    o.display_name AS organization_name,
    COALESCE(s.city,o.city) AS organization_city,
    od.domain_norm AS organization_domain
FROM contacts c
LEFT JOIN contact_emails ce
    ON ce.contact_id=c.id AND ce.is_primary=1
LEFT JOIN contact_phones cp
    ON cp.contact_id=c.id AND cp.is_primary=1
LEFT JOIN contact_employments e
    ON e.id=(
        SELECT e2.id
        FROM contact_employments e2
        WHERE e2.contact_id=c.id AND e2.is_current=1
        ORDER BY e2.confidence DESC,e2.last_seen_at DESC,e2.id DESC
        LIMIT 1
    )
LEFT JOIN organizations o
    ON o.id=e.organization_id
LEFT JOIN organization_sites s
    ON s.id=e.site_id
LEFT JOIN organization_domains od
    ON od.organization_id=o.id AND od.is_primary=1;

CREATE VIEW IF NOT EXISTS v_enrichment_needed AS
SELECT
    m.*,
    CASE WHEN m.last_name IS NULL OR trim(m.last_name)='' THEN 1 ELSE 0 END AS needs_last_name,
    CASE WHEN m.first_name IS NULL OR trim(m.first_name)='' THEN 1 ELSE 0 END AS needs_first_name,
    CASE WHEN m.organization_id IS NULL THEN 1 ELSE 0 END AS needs_organization,
    CASE WHEN COALESCE(m.contact_city,m.organization_city) IS NULL THEN 1 ELSE 0 END AS needs_city,
    CASE WHEN m.primary_email IS NULL THEN 1 ELSE 0 END AS needs_email
FROM v_contact_master m
WHERE
    m.last_name IS NULL OR trim(m.last_name)=''
    OR m.first_name IS NULL OR trim(m.first_name)=''
    OR m.organization_id IS NULL
    OR COALESCE(m.contact_city,m.organization_city) IS NULL
    OR m.primary_email IS NULL;

CREATE VIEW IF NOT EXISTS v_prospectable_contacts AS
SELECT
    m.*,
    ps.status AS prospecting_status,
    ps.total_emails_sent,
    ps.first_contact_at,
    ps.last_contact_at,
    ps.next_eligible_at
FROM v_contact_master m
LEFT JOIN prospecting_state ps ON ps.contact_id=m.contact_id
WHERE
    m.status='active'
    AND m.primary_email IS NOT NULL
    AND m.qualification_status IN ('usable','qualified')
    AND m.vo_relevance IN ('likely','confirmed')
    AND m.email_status NOT IN ('invalid','bounced','unsubscribed')
    AND COALESCE(ps.status,'never_contacted') NOT IN ('suppressed','customer')
    AND (ps.next_eligible_at IS NULL OR ps.next_eligible_at <= strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    AND NOT EXISTS (
        SELECT 1
        FROM suppressions x
        WHERE x.active=1
          AND (
            (x.scope_type='contact' AND x.scope_value=m.rcvo_id)
            OR (x.scope_type='email' AND x.scope_value=m.primary_email)
            OR (x.scope_type='organization' AND m.organization_rcvo_id IS NOT NULL AND x.scope_value=m.organization_rcvo_id)
            OR (x.scope_type='domain' AND m.organization_domain IS NOT NULL AND x.scope_value=m.organization_domain)
          )
    );

CREATE TRIGGER IF NOT EXISTS trg_no_update_source_observations
BEFORE UPDATE ON source_observations
BEGIN
    SELECT RAISE(ABORT,'source_observations is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_source_observations
BEFORE DELETE ON source_observations
BEGIN
    SELECT RAISE(ABORT,'source_observations is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_update_promotion_events
BEFORE UPDATE ON promotion_events
BEGIN
    SELECT RAISE(ABORT,'promotion_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_promotion_events
BEFORE DELETE ON promotion_events
BEGIN
    SELECT RAISE(ABORT,'promotion_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_update_match_decisions
BEFORE UPDATE ON match_decisions
BEGIN
    SELECT RAISE(ABORT,'match_decisions is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_match_decisions
BEFORE DELETE ON match_decisions
BEGIN
    SELECT RAISE(ABORT,'match_decisions is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_update_entity_merges
BEFORE UPDATE ON entity_merges
BEGIN
    SELECT RAISE(ABORT,'entity_merges is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_entity_merges
BEFORE DELETE ON entity_merges
BEGIN
    SELECT RAISE(ABORT,'entity_merges is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_update_prospecting_events
BEFORE UPDATE ON prospecting_events
BEGIN
    SELECT RAISE(ABORT,'prospecting_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_prospecting_events
BEFORE DELETE ON prospecting_events
BEGIN
    SELECT RAISE(ABORT,'prospecting_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_update_suppression_events
BEFORE UPDATE ON suppression_events
BEGIN
    SELECT RAISE(ABORT,'suppression_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_suppression_events
BEFORE DELETE ON suppression_events
BEGIN
    SELECT RAISE(ABORT,'suppression_events is append-only');
END;
