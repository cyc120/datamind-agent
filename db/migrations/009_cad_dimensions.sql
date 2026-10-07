-- Local, authenticated dimension evidence, separate from production/RAG facts.
CREATE TABLE IF NOT EXISTS dimension_measurements (
 measurement_id VARCHAR(128) PRIMARY KEY,
 current_version INT NOT NULL CHECK(current_version>0)
);
CREATE TABLE IF NOT EXISTS dimension_measurement_versions (
 measurement_id VARCHAR(128) NOT NULL REFERENCES dimension_measurements(measurement_id),
 record_version INT NOT NULL CHECK(record_version>0),
 source_bytes BYTEA NOT NULL CHECK(octet_length(source_bytes)<=1048576),
 record JSONB NOT NULL,
 record_sha256 CHAR(64) NOT NULL,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 PRIMARY KEY(measurement_id,record_version)
);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='dimension_measurement_current_fk'
 AND conrelid='dimension_measurements'::regclass) THEN
 ALTER TABLE dimension_measurements ADD CONSTRAINT dimension_measurement_current_fk
 FOREIGN KEY(measurement_id,current_version) REFERENCES dimension_measurement_versions(measurement_id,record_version)
 DEFERRABLE INITIALLY DEFERRED;
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS dimension_bindings (
 binding_id VARCHAR(128) PRIMARY KEY,
 current_version INT NOT NULL CHECK(current_version>0)
);
CREATE TABLE IF NOT EXISTS dimension_binding_versions (
 binding_id VARCHAR(128) NOT NULL REFERENCES dimension_bindings(binding_id),
 binding_version INT NOT NULL CHECK(binding_version>0),
 document_id VARCHAR(64) NOT NULL,
 document_version INT NOT NULL,
 measurement_id VARCHAR(128) NOT NULL,
 measurement_record_version INT NOT NULL,
 review_id VARCHAR(64) NOT NULL REFERENCES cad_association_reviews(review_id),
 record JSONB NOT NULL,
 record_sha256 CHAR(64) NOT NULL,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 PRIMARY KEY(binding_id,binding_version),
 FOREIGN KEY(document_id,document_version) REFERENCES cad_versions(document_id,document_version),
 FOREIGN KEY(measurement_id,measurement_record_version) REFERENCES dimension_measurement_versions(measurement_id,record_version)
);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='dimension_binding_current_fk'
 AND conrelid='dimension_bindings'::regclass) THEN
 ALTER TABLE dimension_bindings ADD CONSTRAINT dimension_binding_current_fk
 FOREIGN KEY(binding_id,current_version) REFERENCES dimension_binding_versions(binding_id,binding_version)
 DEFERRABLE INITIALLY DEFERRED;
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS dimension_approval_events (
 approval_id VARCHAR(128) PRIMARY KEY,
 binding_id VARCHAR(128) NOT NULL,
 binding_version INT NOT NULL,
 sequence_no INT NOT NULL CHECK(sequence_no>0),
 record JSONB NOT NULL,
 record_sha256 CHAR(64) NOT NULL,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 UNIQUE(binding_id,binding_version,sequence_no),
 FOREIGN KEY(binding_id,binding_version) REFERENCES dimension_binding_versions(binding_id,binding_version)
);
CREATE TABLE IF NOT EXISTS dimension_comparisons (
 comparison_id VARCHAR(128) PRIMARY KEY,
 binding_id VARCHAR(128) NOT NULL,
 binding_version INT NOT NULL,
 record JSONB NOT NULL,
 record_sha256 CHAR(64) NOT NULL,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 FOREIGN KEY(binding_id,binding_version) REFERENCES dimension_binding_versions(binding_id,binding_version)
);
GRANT SELECT ON dimension_measurements,dimension_measurement_versions,dimension_bindings,
 dimension_binding_versions,dimension_approval_events,dimension_comparisons TO readonly_user;
