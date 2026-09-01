-- Q09 | the partial index that DOES work (Stage 3b).
-- The predicate is syntactically identical to the index predicate, so the
-- planner's implication prover matches it. Covers roughly 30% of the table.
SELECT po_number, supplier_id, warehouse_id, status, order_date, expected_date
FROM   purchase_orders
WHERE  status IN ('PLACED', 'PARTIALLY_RECEIVED')
  AND  expected_date < DATE '2026-06-01'
ORDER  BY expected_date
LIMIT  200;
