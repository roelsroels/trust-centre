CREATE TABLE IF NOT EXISTS "portal_account" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "password" varchar(128) NOT NULL, "last_login" datetime NULL, "is_superuser" bool NOT NULL, "username" varchar(150) NOT NULL UNIQUE, "first_name" varchar(150) NOT NULL, "last_name" varchar(150) NOT NULL, "is_staff" bool NOT NULL, "is_active" bool NOT NULL, "date_joined" datetime NOT NULL, "email" varchar(254) NOT NULL UNIQUE, "tier" smallint unsigned NOT NULL CHECK ("tier" >= 0));

CREATE TABLE IF NOT EXISTS "otp_totp_totpdevice" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "name" varchar(64) NOT NULL, "confirmed" bool NOT NULL, "key" varchar(80) NOT NULL, "step" smallint unsigned NOT NULL CHECK ("step" >= 0), "t0" bigint NOT NULL, "digits" smallint unsigned NOT NULL CHECK ("digits" >= 0), "tolerance" smallint unsigned NOT NULL CHECK ("tolerance" >= 0), "drift" smallint NOT NULL, "last_t" bigint NOT NULL, "user_id" bigint NOT NULL REFERENCES "portal_account" ("id") DEFERRABLE INITIALLY DEFERRED, "throttling_failure_count" integer unsigned NOT NULL CHECK ("throttling_failure_count" >= 0), "throttling_failure_timestamp" datetime NULL, "created_at" datetime NULL, "last_used_at" datetime NULL);

CREATE INDEX IF NOT EXISTS "otp_totp_totpdevice_user_id_0fb18292" ON "otp_totp_totpdevice" ("user_id");

CREATE TABLE IF NOT EXISTS "portal_branding" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "name" varchar(100) NOT NULL, "introduction" text NOT NULL, "team" text NOT NULL, "logo_url" varchar(200) NOT NULL, "accent" varchar(7) NOT NULL, "ink" varchar(7) NOT NULL, "font" varchar(10) NOT NULL, "access_inbox" varchar(254) NOT NULL, "show_demo_team" bool NOT NULL);

CREATE TABLE IF NOT EXISTS "portal_category" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "name" varchar(100) NOT NULL UNIQUE, "description" varchar(200) NOT NULL);

CREATE TABLE IF NOT EXISTS "portal_document" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "title" varchar(150) NOT NULL, "teaser" text NOT NULL, "minimum_tier" smallint unsigned NOT NULL CHECK ("minimum_tier" >= 0), "agreement" text NOT NULL, "retain_previous" smallint unsigned NOT NULL CHECK ("retain_previous" >= 0), "category_id" bigint NOT NULL REFERENCES "portal_category" ("id") DEFERRABLE INITIALLY DEFERRED, "current_id" bigint NULL REFERENCES "portal_revision" ("id") DEFERRABLE INITIALLY DEFERRED);

CREATE INDEX IF NOT EXISTS "portal_document_category_id_d715394e" ON "portal_document" ("category_id");

CREATE INDEX IF NOT EXISTS "portal_document_current_id_e48ca816" ON "portal_document" ("current_id");

CREATE TABLE IF NOT EXISTS "portal_revision" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "label" varchar(60) NOT NULL, "file" varchar(100) NOT NULL, "created" datetime NOT NULL, "published" datetime NULL, "document_id" bigint NOT NULL REFERENCES "portal_document" ("id") DEFERRABLE INITIALLY DEFERRED);

CREATE INDEX IF NOT EXISTS "portal_revision_document_id_6494e922" ON "portal_revision" ("document_id");

CREATE TABLE IF NOT EXISTS "portal_acceptance" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "digest" varchar(64) NOT NULL, "text" text NOT NULL, "accepted" datetime NOT NULL, "user_id" bigint NULL REFERENCES "portal_account" ("id") DEFERRABLE INITIALLY DEFERRED, "document_id" bigint NOT NULL REFERENCES "portal_document" ("id") DEFERRABLE INITIALLY DEFERRED, CONSTRAINT "unique_acceptance" UNIQUE ("user_id", "document_id", "digest"));

CREATE INDEX IF NOT EXISTS "portal_acceptance_user_id_2b70707d" ON "portal_acceptance" ("user_id");

CREATE INDEX IF NOT EXISTS "portal_acceptance_document_id_6778a0b3" ON "portal_acceptance" ("document_id");

CREATE TABLE IF NOT EXISTS "portal_accessrequest" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "tier" smallint unsigned NOT NULL CHECK ("tier" >= 0), "requested" datetime NOT NULL, "user_id" bigint NOT NULL REFERENCES "portal_account" ("id") DEFERRABLE INITIALLY DEFERRED, "document_id" bigint NOT NULL REFERENCES "portal_document" ("id") DEFERRABLE INITIALLY DEFERRED, "handled" datetime NULL);

CREATE INDEX IF NOT EXISTS "portal_accessrequest_user_id_fe24046c" ON "portal_accessrequest" ("user_id");

CREATE INDEX IF NOT EXISTS "portal_accessrequest_document_id_089eab81" ON "portal_accessrequest" ("document_id");


CREATE TABLE IF NOT EXISTS "portal_subscriber" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "email" varchar(254) NOT NULL UNIQUE, "token" char(32) NOT NULL UNIQUE, "status" varchar(12) NOT NULL, "changed" datetime NOT NULL);

CREATE TABLE IF NOT EXISTS "portal_outgoingmail" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "email" varchar(254) NOT NULL, "subject" varchar(200) NOT NULL, "body" text NOT NULL, "expected_status" varchar(12) NOT NULL, "subscription_token" char(32) NULL, "attempts" smallint unsigned NOT NULL CHECK ("attempts" >= 0), "next_attempt" datetime NOT NULL, "subscriber_id" bigint NULL REFERENCES "portal_subscriber" ("id") DEFERRABLE INITIALLY DEFERRED);

CREATE INDEX IF NOT EXISTS "portal_outgoingmail_subscriber_id_d0fae98c" ON "portal_outgoingmail" ("subscriber_id");
CREATE TABLE IF NOT EXISTS trust_auth_limit (
    key TEXT PRIMARY KEY,
    hits INTEGER NOT NULL,
    expires INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS trust_font (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    regular TEXT NOT NULL,
    medium TEXT NOT NULL,
    italic TEXT NOT NULL,
    medium_italic TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trust_sso_config (
    id INTEGER PRIMARY KEY CHECK (id=1),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    label TEXT NOT NULL,
    issuer TEXT NOT NULL,
    client_id TEXT NOT NULL,
    token_auth TEXT NOT NULL CHECK (token_auth IN ('client_secret_basic', 'client_secret_post'))
);

CREATE TABLE IF NOT EXISTS trust_sso_identity (
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES portal_account(id) ON DELETE CASCADE,
    PRIMARY KEY (issuer, subject),
    UNIQUE (issuer, account_id)
);
