-- 从项目根目录执行；只使用已有索引实验副本。
.bail on
.headers off
.mode list
.open 'file:data/practice/index_lab.db?mode=rw'

-- 每次实验开始前，确认上一次回滚没有留下演示数据。
.print BEFORE_TRANSACTION
SELECT COUNT(*) AS transaction_lab_rows
FROM items
WHERE category = 'transaction-lab';

-- 这两条写入在同一个事务内：此时连接自己可以看见它们。
BEGIN;
INSERT INTO items (id, name, price, category)
VALUES (1200001, 'Transaction Lab A', 1.00, 'transaction-lab');
INSERT INTO items (id, name, price, category)
VALUES (1200002, 'Transaction Lab B', 2.00, 'transaction-lab');

.print INSIDE_TRANSACTION
SELECT id, name, price
FROM items
WHERE category = 'transaction-lab'
ORDER BY id;

-- 模拟业务步骤没有完成，因此显式撤销整个事务。
ROLLBACK;

.print AFTER_ROLLBACK
SELECT COUNT(*) AS transaction_lab_rows
FROM items
WHERE category = 'transaction-lab';
