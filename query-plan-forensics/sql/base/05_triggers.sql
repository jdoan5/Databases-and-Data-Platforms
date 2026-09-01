-- ============================================================================
-- 05_triggers.sql  -  trigger-driven business rules.
-- After this runs, INSERTs into stock_movements automatically maintain
-- stock_levels, so application code never has to do both.
-- ============================================================================

DROP TRIGGER  IF EXISTS trg_stock_movement_apply ON stock_movements;
DROP TRIGGER  IF EXISTS trg_stock_levels_touch   ON stock_levels;
DROP TRIGGER  IF EXISTS trg_poi_update_po_status ON purchase_order_items;

DROP FUNCTION IF EXISTS apply_stock_movement();
DROP FUNCTION IF EXISTS touch_stock_levels();
DROP FUNCTION IF EXISTS update_po_status_from_items();


-- ----------------------------------------------------------------------------
-- 1. apply_stock_movement
--    On INSERT into stock_movements, add/subtract from stock_levels.
--    Uses INSERT ... ON CONFLICT (UPSERT) so the row is created if missing.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION apply_stock_movement()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_delta INT;
BEGIN
    v_delta := CASE NEW.movement_type
        WHEN 'IN'           THEN  NEW.quantity
        WHEN 'RETURN'       THEN  NEW.quantity
        WHEN 'TRANSFER_IN'  THEN  NEW.quantity
        WHEN 'OUT'          THEN -NEW.quantity
        WHEN 'TRANSFER_OUT' THEN -NEW.quantity
        WHEN 'ADJUSTMENT'   THEN  NEW.quantity   -- positive only by table CHECK
        ELSE 0
    END;

    INSERT INTO stock_levels (product_id, warehouse_id, quantity, last_updated)
    VALUES (NEW.product_id, NEW.warehouse_id, GREATEST(v_delta, 0), NOW())
    ON CONFLICT (product_id, warehouse_id)
    DO UPDATE
        SET quantity     = stock_levels.quantity + v_delta,
            last_updated = NOW();

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_stock_movement_apply
AFTER INSERT ON stock_movements
FOR EACH ROW EXECUTE FUNCTION apply_stock_movement();


-- ----------------------------------------------------------------------------
-- 2. touch_stock_levels
--    Whenever stock_levels.quantity is updated directly (rare, manual fix),
--    keep last_updated honest.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION touch_stock_levels()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.last_updated := NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_stock_levels_touch
BEFORE UPDATE OF quantity ON stock_levels
FOR EACH ROW EXECUTE FUNCTION touch_stock_levels();


-- ----------------------------------------------------------------------------
-- 3. update_po_status_from_items
--    When line-item receipts change, roll the parent PO's status forward.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_po_status_from_items()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_ordered  INT;
    v_received INT;
    v_target   po_status_enum;
    v_po_id    INT := COALESCE(NEW.po_id, OLD.po_id);
BEGIN
    SELECT SUM(quantity_ordered), SUM(quantity_received)
      INTO v_ordered, v_received
    FROM purchase_order_items
    WHERE po_id = v_po_id;

    v_target := CASE
        WHEN v_received = 0                          THEN 'PLACED'
        WHEN v_received < v_ordered                  THEN 'PARTIALLY_RECEIVED'
        WHEN v_received >= v_ordered                 THEN 'RECEIVED'
    END;

    UPDATE purchase_orders
       SET status        = v_target,
           received_date = CASE WHEN v_target = 'RECEIVED' AND received_date IS NULL
                                THEN CURRENT_DATE ELSE received_date END
     WHERE po_id  = v_po_id
       AND status NOT IN ('DRAFT', 'CANCELLED');   -- don't disturb manual states

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_poi_update_po_status
AFTER INSERT OR UPDATE OF quantity_received ON purchase_order_items
FOR EACH ROW EXECUTE FUNCTION update_po_status_from_items();


-- ============================================================================
-- Try it out -- run each block and watch stock_levels / purchase_orders change.
-- ============================================================================

-- A) Receive 20 headphones at the East DC.  v_current_stock will reflect it.
-- INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reference_type, notes)
-- SELECT p.product_id, w.warehouse_id, 'IN', 20, 'MANUAL', 'Walk-in delivery'
-- FROM products p, warehouses w
-- WHERE p.sku = 'ELEC-AUD-001' AND w.code = 'WH-EAST';
--
-- SELECT * FROM v_current_stock WHERE sku='ELEC-AUD-001' ORDER BY warehouse_code;


-- B) Sell 2 standing desks from West DC.
-- INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reference_type, reference_id, notes)
-- SELECT p.product_id, w.warehouse_id, 'OUT', 2, 'SALES_ORDER', 9999, 'Customer pickup'
-- FROM products p, warehouses w
-- WHERE p.sku = 'FURN-DSK-001' AND w.code = 'WH-WEST';


-- C) Finish receiving PO-2026-0002 -- status should roll to RECEIVED.
-- UPDATE purchase_order_items
--    SET quantity_received = quantity_ordered
--  WHERE po_id = (SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0002');
--
-- SELECT po_number, status, received_date
-- FROM purchase_orders WHERE po_number = 'PO-2026-0002';
