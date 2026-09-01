-- Q08 | three-table join with a date filter.
-- purchase_orders x purchase_order_items x suppliers. 500k line items, the
-- filter selective enough that join order matters.
SELECT s.name AS supplier,
       count(DISTINCT po.po_id)                        AS orders,
       sum(poi.quantity_ordered)                       AS units,
       round(sum(poi.quantity_ordered * poi.unit_cost), 2) AS spend
FROM   purchase_orders po
JOIN   purchase_order_items poi ON poi.po_id = po.po_id
JOIN   suppliers s ON s.supplier_id = po.supplier_id
WHERE  po.order_date >= DATE '2026-01-01'
  AND  po.status <> 'CANCELLED'
GROUP  BY s.name
ORDER  BY spend DESC
LIMIT  20;
