-- SPDX-License-Identifier: MIT OR Apache-2.0
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
ALTER TABLE bonafides.professional_claims DROP CONSTRAINT professional_claims_kind_check;
ALTER TABLE bonafides.professional_claims ADD CONSTRAINT professional_claims_kind_check
 CHECK (kind IN ('identity','experience','education','skill','project','certification',
                'preference','other','language','publication','writing_fact'));
