-- 05_prepare.sql — schema changes made while the tables are still empty and cheap.
-- Run after sql/base/01_schema.sql, before 10_generate.sql.

-- movement_id: SERIAL (int4) is fine at 5M rows but wrong in principle for an
-- append-only ledger. Changing it now is free; at 5M rows it is a table rewrite.
ALTER TABLE stock_movements ALTER COLUMN movement_id TYPE BIGINT;

-- created_at must be NOT NULL before it can be a range partition key (Stage 5).
ALTER TABLE stock_movements ALTER COLUMN created_at SET NOT NULL;

-- Generator scratch space. Separate schema so `\dt public.*` stays honest about
-- what is actually part of the model.
DROP SCHEMA IF EXISTS bench_gen CASCADE;
CREATE SCHEMA bench_gen;
