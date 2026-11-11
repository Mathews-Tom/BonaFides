-- SPDX-License-Identifier: MIT OR Apache-2.0
CREATE TABLE bonafides.installation_updates (
 singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
 enabled boolean NOT NULL DEFAULT false,
 version bigint NOT NULL DEFAULT 1 CHECK (version>0),
 last_checked_at timestamptz,
 automatic_day date,
 outcome text NOT NULL DEFAULT 'never' CHECK (outcome IN ('never','newer','current','no_release','unknown')),
 latest_version text,
 latest_url text,
 dismissed_version text,
 error_code text,
 lease_id uuid,
 lease_until timestamptz,
 CHECK ((lease_id IS NULL)=(lease_until IS NULL)),
 CHECK ((latest_version IS NULL)=(latest_url IS NULL))
);
INSERT INTO bonafides.installation_updates(singleton) VALUES(true);
REVOKE ALL ON bonafides.installation_updates FROM PUBLIC;
GRANT SELECT,UPDATE ON bonafides.installation_updates TO bonafides_runtime;
