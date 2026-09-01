-- ============================================================================
-- 06_procedures.sql  -  stored procedures (functions) + transactions practice.
-- Run AFTER 01-05.  Demonstrates atomicity: a routine either fully succeeds
-- or leaves the database untouched.
-- ============================================================================

DROP FUNCTION IF EXISTS transfer_stock(VARCHAR, VARCHAR, VARCHAR, INT);
DROP FUNCTION IF EXISTS receive_purchase_order(VARCHAR);


-- ----------------------------------------------------------------------------
-- transfer_stock  -  move units of one product between two warehouses.
--
-- Writes BOTH a TRANSFER_OUT (source) and a TRANSFER_IN (destination).
-- The 05 trigger then adjusts stock_levels for each side automatically.
--
-- Because this is one function call = one transaction, if ANY statement
-- raises (e.g. the source doesn't have enough stock), the whole thing rolls
-- back. You can never end up with a TRANSFER_OUT that has no matching IN.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION transfer_stock(
    p_sku       VARCHAR,
    p_from_code VARCHAR,
    p_to_code   VARCHAR,
    p_qty       INT
)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_product_id INT;
    v_from_wh    INT;
    v_to_wh      INT;
    v_available  INT;
BEGIN
    -- 1. Validate inputs up front (fail fast, with a friendly message).
    IF p_qty <= 0 THEN
        RAISE EXCEPTION 'Transfer quantity must be positive (got %)', p_qty;
    END IF;

    SELECT product_id INTO v_product_id FROM products   WHERE sku  = p_sku;
    IF v_product_id IS NULL THEN
        RAISE EXCEPTION 'Unknown SKU: %', p_sku;
    END IF;

    SELECT warehouse_id INTO v_from_wh FROM warehouses WHERE code = p_from_code;
    SELECT warehouse_id INTO v_to_wh   FROM warehouses WHERE code = p_to_code;
    IF v_from_wh IS NULL THEN RAISE EXCEPTION 'Unknown source warehouse: %',      p_from_code; END IF;
    IF v_to_wh   IS NULL THEN RAISE EXCEPTION 'Unknown destination warehouse: %', p_to_code;   END IF;
    IF v_from_wh = v_to_wh THEN
        RAISE EXCEPTION 'Source and destination warehouse are the same (%)', p_from_code;
    END IF;

    -- 2. Make sure the source actually has the stock.
    SELECT quantity INTO v_available
    FROM   stock_levels
    WHERE  product_id = v_product_id AND warehouse_id = v_from_wh;

    v_available := COALESCE(v_available, 0);
    IF v_available < p_qty THEN
        RAISE EXCEPTION 'Insufficient stock at %: have %, need %',
            p_from_code, v_available, p_qty;
    END IF;

    -- 3. Two ledger entries. Triggers keep stock_levels in sync.
    --    Both INSERTs commit together or not at all.
    INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reference_type, notes)
    VALUES (v_product_id, v_from_wh, 'TRANSFER_OUT', p_qty, 'TRANSFER', format('To %s',   p_to_code));

    INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reference_type, notes)
    VALUES (v_product_id, v_to_wh,   'TRANSFER_IN',  p_qty, 'TRANSFER', format('From %s', p_from_code));

    RETURN format('OK: transferred %s x %s from %s to %s', p_qty, p_sku, p_from_code, p_to_code);
END;
$$;


-- ----------------------------------------------------------------------------
-- receive_purchase_order  -  receive every outstanding line on a PO at once.
--
-- For each line still owing units it:
--   (a) inserts an 'IN' movement  -> 05 trigger bumps stock_levels
--   (b) sets quantity_received = quantity_ordered
--       -> 05 trigger rolls the PO's status forward to RECEIVED
--
-- One call wires together everything you've built: ledger, stock, PO status.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION receive_purchase_order(p_po_number VARCHAR)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_po_id     INT;
    v_warehouse INT;
    r           RECORD;
    v_lines     INT := 0;
    v_units     INT := 0;
BEGIN
    SELECT po_id, warehouse_id INTO v_po_id, v_warehouse
    FROM   purchase_orders
    WHERE  po_number = p_po_number;

    IF v_po_id IS NULL THEN
        RAISE EXCEPTION 'Unknown purchase order: %', p_po_number;
    END IF;

    -- Loop over each line that still has units outstanding.
    FOR r IN
        SELECT po_item_id, product_id,
               (quantity_ordered - quantity_received) AS outstanding
        FROM   purchase_order_items
        WHERE  po_id = v_po_id
          AND  quantity_received < quantity_ordered
    LOOP
        INSERT INTO stock_movements
            (product_id, warehouse_id, movement_type, quantity, reference_type, reference_id, notes)
        VALUES
            (r.product_id, v_warehouse, 'IN', r.outstanding,
             'PURCHASE_ORDER', v_po_id, format('Receipt of %s', p_po_number));

        UPDATE purchase_order_items
           SET quantity_received = quantity_ordered
         WHERE po_item_id = r.po_item_id;

        v_lines := v_lines + 1;
        v_units := v_units + r.outstanding;
    END LOOP;

    IF v_lines = 0 THEN
        RETURN format('PO %s was already fully received - nothing to do.', p_po_number);
    END IF;

    RETURN format('Received PO %s: %s line(s), %s unit(s). Status is now %s.',
        p_po_number, v_lines, v_units,
        (SELECT status FROM purchase_orders WHERE po_id = v_po_id));
END;
$$;


-- ============================================================================
-- DEMO  -  run these one at a time and watch the results.
-- ============================================================================

-- --- transfer_stock -------------------------------------------------------

-- Before: headphones at WEST and EAST.
-- SELECT sku, warehouse_code, quantity FROM v_current_stock
-- WHERE sku='ELEC-AUD-001' AND warehouse_code IN ('WH-WEST','WH-EAST')
-- ORDER BY warehouse_code;

-- Move 10 units EAST -> WEST (returns an "OK: ..." message):
-- SELECT transfer_stock('ELEC-AUD-001', 'WH-EAST', 'WH-WEST', 10);

-- After: EAST dropped 10, WEST gained 10. Net stock unchanged, just relocated.
-- (re-run the "Before" query)


-- --- atomicity: a transfer that MUST fail ---------------------------------
-- Try to move more than exists. It raises, and NOTHING is written -- verify
-- afterwards that no stray TRANSFER_OUT landed in the ledger.
-- SELECT transfer_stock('ELEC-AUD-001', 'WH-EAST', 'WH-WEST', 999999);
--
-- SELECT * FROM stock_movements
-- WHERE reference_type='TRANSFER' AND quantity=999999;   -- expect 0 rows


-- --- receive_purchase_order -----------------------------------------------

-- PO-2026-0003 is 'PLACED' with nothing received yet:
-- SELECT * FROM v_purchase_order_summary WHERE po_number='PO-2026-0003';

-- Receive it all in one call:
-- SELECT receive_purchase_order('PO-2026-0003');

-- Now: status flipped to RECEIVED, received_date set, and the paper + stapler
-- stock at WH-EAST rose by the received quantities.
-- SELECT * FROM v_purchase_order_summary WHERE po_number='PO-2026-0003';
-- SELECT sku, warehouse_code, quantity FROM v_current_stock
-- WHERE sku IN ('OFFC-PAP-001','OFFC-STP-001') AND warehouse_code='WH-EAST';
