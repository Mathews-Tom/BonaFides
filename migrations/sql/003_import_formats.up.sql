-- SPDX-License-Identifier: MIT OR Apache-2.0
ALTER TABLE bonafides.import_batches
 DROP CONSTRAINT import_batches_media_type_check,
 ADD CONSTRAINT import_batches_media_type_check CHECK (media_type IN
 ('application/pdf','text/plain','text/markdown','application/vnd.openxmlformats-officedocument.wordprocessingml.document'));
