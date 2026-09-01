-- Q02 | v_stock_valuation | the materialization candidate (Stage 6).
-- Full aggregate over stock_levels x products, six groups out. Nothing to index;
-- the only lever is whether the answer is computed or stored.
SELECT warehouse_code, distinct_skus, total_units,
       valuation_at_cost, valuation_at_retail
FROM   v_stock_valuation
ORDER  BY valuation_at_cost DESC;
