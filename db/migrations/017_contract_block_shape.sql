-- Close the contract-block shape (F07): every key a validated contract block may carry
-- gets a column in `document_contract_blocks`, so "the contract accepted this block"
-- implies "the store can hold every field of it".
--
-- Migration 015 created the table with only the page-independent subset, so a validated
-- block that carried `heading_level`/`table_cells` (Office) or `parser_version`/
-- `source_version`/`quality` (every adapter) was silently dropped on write: the contract
-- said "valid" and the store kept less than the contract promised. The block keys are
-- pinned in datamind/parse_contract.py (BLOCK_REQUIRED_FIELDS + BLOCK_OPTIONAL_FIELDS)
-- and tests/test_parse_contract.py asserts the 1:1 mapping against this file.
--
-- Idempotent: every statement is ADD COLUMN IF NOT EXISTS. All columns stay nullable
-- because rows already written by earlier parses carry no value for them and no value
-- may be fabricated for history; a validated block always supplies the required ones.
ALTER TABLE document_contract_blocks
 ADD COLUMN IF NOT EXISTS source_version VARCHAR(128);
ALTER TABLE document_contract_blocks
 ADD COLUMN IF NOT EXISTS parser_version VARCHAR(64);
ALTER TABLE document_contract_blocks
 ADD COLUMN IF NOT EXISTS quality JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE document_contract_blocks
 ADD COLUMN IF NOT EXISTS heading_level INT;
ALTER TABLE document_contract_blocks
 ADD COLUMN IF NOT EXISTS table_cells JSONB;
ALTER TABLE document_contract_blocks
 ADD COLUMN IF NOT EXISTS formula_latex TEXT;
ALTER TABLE document_contract_blocks
 ADD COLUMN IF NOT EXISTS ocr_confidence DOUBLE PRECISION;
ALTER TABLE document_contract_blocks
 ADD COLUMN IF NOT EXISTS page_no INT;
ALTER TABLE document_contract_blocks
 ADD COLUMN IF NOT EXISTS bbox JSONB;

COMMENT ON COLUMN document_contract_blocks.source_version IS
 'Block source version (parser-reported); NULL only for rows written before 017';
COMMENT ON COLUMN document_contract_blocks.parser_version IS
 'Parser version that produced the block; NULL only for rows written before 017';
COMMENT ON COLUMN document_contract_blocks.quality IS
 'Block quality payload as validated by datamind/parse_contract.py';
COMMENT ON COLUMN document_contract_blocks.heading_level IS
 'Heading depth for title blocks; NULL when the block is not a heading';
COMMENT ON COLUMN document_contract_blocks.table_cells IS
 'Table cell grid as parsed; NULL when the block is not a table';
COMMENT ON COLUMN document_contract_blocks.formula_latex IS
 'Formula source for formula blocks; NULL otherwise';
COMMENT ON COLUMN document_contract_blocks.ocr_confidence IS
 'OCR confidence for scanned origin; NULL when not applicable';
COMMENT ON COLUMN document_contract_blocks.page_no IS
 'Page number for page-shaped origin carried through the unified contract; NULL otherwise';
COMMENT ON COLUMN document_contract_blocks.bbox IS
 'Source bounding box for page-shaped origin; NULL otherwise';
