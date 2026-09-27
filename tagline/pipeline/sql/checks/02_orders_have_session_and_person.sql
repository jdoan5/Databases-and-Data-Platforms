-- @check Every order has a person; an order with a session_key is in fct_sessions and, unless its own purchase
--     carried a user_id, has that session's person and rule; an order with no session (a cookieless purchase)
--     is in the documented no-session bucket; every order line belongs to an order.
SELECT 'order without a person' AS problem, o.source, o.order_id
FROM `{{ project }}.{{ marts }}.fct_orders` AS o
WHERE o.person_id IS NULL OR o.identity_rule IS NULL
UNION ALL
SELECT 'order in a session missing from fct_sessions', o.source, o.order_id
FROM `{{ project }}.{{ marts }}.fct_orders` AS o
LEFT JOIN `{{ project }}.{{ marts }}.fct_sessions` AS s USING (source, session_key)
WHERE o.session_key IS NOT NULL AND s.session_key IS NULL
UNION ALL
SELECT "order's person or rule does not follow from its session", o.source, o.order_id
FROM `{{ project }}.{{ marts }}.fct_orders` AS o
JOIN `{{ project }}.{{ marts }}.fct_sessions` AS s USING (source, session_key)
WHERE o.identity_rule != 'signed_in_purchase'
  AND (o.person_id != s.person_id OR o.identity_rule != s.identity_rule)
UNION ALL
SELECT 'order with no session outside the no-session rules', o.source, o.order_id
FROM `{{ project }}.{{ marts }}.fct_orders` AS o
WHERE o.session_key IS NULL
  AND o.identity_rule NOT IN ('signed_in_purchase', 'device_without_session', 'cookieless')
UNION ALL
SELECT 'order line without order', i.source, i.order_id
FROM `{{ project }}.{{ marts }}.fct_order_items` AS i
LEFT JOIN `{{ project }}.{{ marts }}.fct_orders` AS o USING (source, order_id)
WHERE o.order_id IS NULL
