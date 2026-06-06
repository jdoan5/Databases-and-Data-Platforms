-- ============================================================================
-- EXERCISES.sql  -  10 graded challenges, easy -> hard.
-- Write your answer under each question, run it (cursor in the block + Cmd+Enter),
-- then check EXERCISES_SOLUTIONS.sql.  Try each one BEFORE peeking.
-- Target: inventory_mgmt.public
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Q1 (easy) - JOIN
-- List every product in the 'Audio' category: sku, name, unit_price.
-- Hint: products JOIN categories, filter on category name.
-- ----------------------------------------------------------------------------
-- your answer here:



-- ----------------------------------------------------------------------------
-- Q2 (easy) - LEFT JOIN + GROUP BY
-- How many products are in each category? Show category name and the count,
-- INCLUDING categories that currently have zero products (count = 0).
-- Hint: start FROM categories and LEFT JOIN products.
-- ----------------------------------------------------------------------------



-- ----------------------------------------------------------------------------
-- Q3 (medium) - aggregate over a join
-- For each warehouse, show total units on hand and total inventory value at
-- cost (sum of quantity * unit_cost). Order by value, highest first.
-- ----------------------------------------------------------------------------



-- ----------------------------------------------------------------------------
-- Q4 (medium) - filtering a join
-- Show every (product, warehouse) sitting BELOW its reorder point:
-- sku, product name, warehouse code, quantity, reorder_point.
-- (Yes, v_low_stock_items already does this - write it yourself from the base
--  tables to prove you can.)
-- ----------------------------------------------------------------------------



-- ----------------------------------------------------------------------------
-- Q5 (medium) - LEFT JOIN to keep empties + aggregate
-- For each supplier, total value of all their purchase orders
-- (sum of quantity_ordered * unit_cost across all their PO lines).
-- Include suppliers with NO purchase orders (show 0).
-- Hint: COALESCE(SUM(...), 0).
-- ----------------------------------------------------------------------------



-- ----------------------------------------------------------------------------
-- Q6 (medium-hard) - aggregate + ORDER BY + LIMIT
-- The 3 most valuable products by total inventory value (summed across all
-- warehouses: quantity * unit_cost). Show sku, name, total_value.
-- ----------------------------------------------------------------------------



-- ----------------------------------------------------------------------------
-- Q7 (hard) - window function
-- Rank warehouses by total inventory value (at cost), and show each
-- warehouse's PERCENTAGE share of the company-wide total.
-- Hint: SUM(...) OVER () gives the grand total alongside each row;
--       use ROUND(100.0 * part / whole, 1) for the percentage.
-- ----------------------------------------------------------------------------



-- ----------------------------------------------------------------------------
-- Q8 (hard) - anti-join
-- Which products have NEVER had an 'OUT' movement (i.e. never been shipped)?
-- Show sku and name.
-- Hint: NOT EXISTS (SELECT 1 FROM stock_movements ...), or a LEFT JOIN ... IS NULL.
-- ----------------------------------------------------------------------------



-- ----------------------------------------------------------------------------
-- Q9 (hard) - computed ratio, NULL-safe
-- For each purchase order show po_number, status, and percent_received =
-- 100 * total_received / total_ordered, rounded to 1 decimal.
-- Guard against divide-by-zero (a PO with 0 ordered should show 0, not error).
-- Hint: NULLIF(total_ordered, 0).
-- ----------------------------------------------------------------------------



-- ----------------------------------------------------------------------------
-- Q10 (challenge) - the ledger, netted
-- "Net movement" per product over the last 30 days: sum of all inbound
-- quantities (IN, RETURN, TRANSFER_IN) MINUS all outbound (OUT, TRANSFER_OUT).
-- Show sku, name, net_change. Exclude products whose net is exactly 0.
-- Hint: SUM(CASE WHEN movement_type IN (...) THEN quantity ELSE -quantity END)
--       and a HAVING clause to drop the zeros.
-- ----------------------------------------------------------------------------
