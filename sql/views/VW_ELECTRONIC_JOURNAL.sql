/*  Electronic General (electronic journal) - one row per item line and per payment line.

    Sources   : dbo.ITEM_WISE_TRANSACTIONS    -> Line type 'Sales'
                dbo.PAYMENT_WISE_TRANSACTIONS -> Line type 'Payment'
                dbo.DIM_PRODUCT               -> Product name (MAJ_CAT)
                dbo.STORE_PLANT_MASTER        -> Store code (ST_CD) and Store name (ST_FULL_NM), matched on ST_CD = STORE
    Tx type   : 'Return' when the receipt's item quantity is negative (or, with no item lines,
                its payment amount is negative); otherwise 'Sales'. Same value on every line
                of the receipt.
    Product   : ITEMID -> BIGINT -> VARCHAR, left-padded with zeros to 18 digits = MATNR.
                VARCHAR (not NVARCHAR) so the lookup can use IX_MATNR.
    Net amount: NETAMOUNTINCLTAX.   Tendered: AMOUNTTENDERED on payment lines, 0 on item lines.
    Shift     : SHIFT from the line's own table.

    Query with a date filter and ORDER BY, e.g.
        SELECT * FROM dbo.VW_ELECTRONIC_JOURNAL
        WHERE [Transaction date] BETWEEN '2026-09-29' AND '2026-09-29'
        ORDER BY [Transaction date], [Transaction time], [Transaction number], SortLine, [Line number];
*/
CREATE OR ALTER VIEW dbo.VW_ELECTRONIC_JOURNAL
AS
-- BODY (app.py wraps the text below this line in a subquery when the view does not exist yet)
SELECT
    l.TRANSDATE AS [Transaction date],
    STUFF(STUFF(RIGHT('000000' + CAST(l.TRANSTIME AS varchar(6)), 6), 5, 0, ':'), 3, 0, ':') AS [Transaction time],
    l.TERMINAL AS [Cash register number],
    CASE WHEN COALESCE(it.qty, pt.amount, 0) < 0 THEN 'Return' ELSE 'Sales' END AS [Transaction type],
    l.TRANSACTIONID AS [Transaction number],
    l.RECEIPTID AS [Receipt number],
    l.LINE_TYPE AS [Line type],
    l.ITEMID AS [Item number],
    l.MAJ_CAT AS [Product name],
    l.QTY AS [Quantity],
    l.PRICE AS [Price],
    l.TAXAMOUNT AS [Tax amount],
    l.DISCAMOUNT AS [Cash discount amount],
    l.STAFF AS [Staff],
    l.NETAMOUNTINCLTAX AS [Net amount],
    l.MOP_TYPE AS [Payment method],
    l.TENDERED AS [Tendered],
    l.STORE AS [Store code],             -- = STORE_PLANT_MASTER.ST_CD
    sm.ST_FULL_NM AS [Store name],
    l.SHIFT AS [Shift],
    l.STORE AS [Store],
    l.LINENUM AS [Line number],
    l.SortLine AS SortLine
FROM (
    -- item lines
    SELECT
        i.TRANSDATE, i.TRANSTIME, i.TERMINALID AS TERMINAL, i.STORE,
        i.TRANSACTIONID, i.RECEIPTID, 'Sales' AS LINE_TYPE, 1 AS SortLine, i.LINENUM,
        i.ITEMID, p.MAJ_CAT,
        i.QTY, i.PRICE, i.TAXAMOUNT, i.DISCAMOUNT, i.STAFFID AS STAFF, i.NETAMOUNTINCLTAX,
        CAST(NULL AS varchar(20)) AS MOP_TYPE, CAST(0 AS decimal(18, 2)) AS TENDERED, i.SHIFT
    FROM dbo.ITEM_WISE_TRANSACTIONS i
    LEFT JOIN dbo.DIM_PRODUCT p WITH (NOLOCK)
        ON p.MATNR = RIGHT(REPLICATE('0', 18) + CAST(TRY_CAST(i.ITEMID AS bigint) AS varchar(20)), 18)

    UNION ALL

    -- payment lines
    SELECT
        y.TRANSDATE, y.TRANSTIME, y.TERMINAL, y.STORE,
        y.TRANSACTIONID, y.RECEIPTID, 'Payment', 2, y.LINENUM,
        NULL, NULL,
        0, 0, 0, 0, y.STAFF, 0,
        y.MOP_TYPE, y.AMOUNTTENDERED, y.SHIFT
    FROM dbo.PAYMENT_WISE_TRANSACTIONS y
) l
LEFT JOIN dbo.STORE_PLANT_MASTER sm WITH (NOLOCK) ON sm.ST_CD = l.STORE
-- receipt-level totals decide Sales vs Return for every line of the receipt
LEFT JOIN (
    SELECT STORE, RECEIPTID, SUM(QTY) AS qty
    FROM dbo.ITEM_WISE_TRANSACTIONS
    GROUP BY STORE, RECEIPTID
) it ON it.STORE = l.STORE AND it.RECEIPTID = l.RECEIPTID
LEFT JOIN (
    SELECT STORE, RECEIPTID, SUM(AMOUNTTENDERED) AS amount
    FROM dbo.PAYMENT_WISE_TRANSACTIONS
    GROUP BY STORE, RECEIPTID
) pt ON pt.STORE = l.STORE AND pt.RECEIPTID = l.RECEIPTID;
