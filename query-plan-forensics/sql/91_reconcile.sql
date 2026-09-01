-- 91_reconcile.sql — the self-audit, ported from the Migration Drill candidate.
--
-- Three real defects in the committed schema. Each is reproduced here, so the
-- writeup can show the failure before it shows the fix.
--
--   [1] 05_triggers.sql:39 — GREATEST(v_delta, 0) on the INSERT arm of the
--       upsert. A first-ever OUT against a location with no stock_levels row
--       writes 0 instead of tripping CHECK (quantity >= 0) at 01_schema.sql:108.
--       The clamp swallows the very error it looks like it is preventing.
--
--   [2] 01_schema.sql:162 — CHECK (quantity > 0) on stock_movements makes a
--       negative cycle-count ADJUSTMENT impossible to record. The trigger's own
--       comment at 05_triggers.sql:34 concedes it: "positive only by table CHECK".
--
--   [3] The ledger and the balances have never agreed, because stock_levels is
--       seeded independently of stock_movements. NOT because the bulk load
--       disables the trigger — the balances predate the movements entirely.

\echo '== [1] reproduce the silent zero =='
BEGIN;
  -- Pick a (product, warehouse) pair with no stock_levels row.
  DELETE FROM stock_levels WHERE product_id = 1 AND warehouse_id = 1;

  INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity,
                               reference_type, notes)
  VALUES (1, 1, 'OUT', 25, 'MANUAL', 'shipping 25 units we do not have');

  -- Expected under a correct implementation: the INSERT above raises.
  -- Actual: it succeeds, and this row reads 0.
  SELECT product_id, warehouse_id, quantity AS should_have_raised_not_returned_zero
  FROM   stock_levels WHERE product_id = 1 AND warehouse_id = 1;
ROLLBACK;

\echo ''
\echo '== [2] a negative adjustment cannot be written at all =='
DO $$
BEGIN
    INSERT INTO stock_movements (product_id, warehouse_id, movement_type,
                                 quantity, reference_type, notes)
    VALUES (1, 1, 'ADJUSTMENT', -12, 'MANUAL', 'cycle count found 12 units short');
    RAISE NOTICE 'UNEXPECTED: the negative adjustment was accepted.';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'As expected: %', SQLERRM;
    RAISE NOTICE 'Shrinkage is a fact of inventory. This schema cannot record it.';
END $$;

\echo ''
\echo '== [3] ledger vs. balances — the divergence, quantified =='
WITH ledger AS (
    SELECT product_id, warehouse_id,
           sum(CASE movement_type
                 WHEN 'IN'           THEN  quantity
                 WHEN 'RETURN'       THEN  quantity
                 WHEN 'TRANSFER_IN'  THEN  quantity
                 WHEN 'OUT'          THEN -quantity
                 WHEN 'TRANSFER_OUT' THEN -quantity
                 WHEN 'ADJUSTMENT'   THEN  quantity
               END) AS ledger_qty
    FROM   stock_movements
    GROUP  BY 1, 2
)
SELECT count(*)                                             AS pairs_compared,
       count(*) FILTER (WHERE sl.quantity IS DISTINCT FROM l.ledger_qty)
                                                            AS pairs_disagreeing,
       round(avg(abs(sl.quantity - l.ledger_qty)))          AS mean_abs_gap,
       max(abs(sl.quantity - l.ledger_qty))                 AS worst_gap
FROM   ledger l
JOIN   stock_levels sl USING (product_id, warehouse_id);
