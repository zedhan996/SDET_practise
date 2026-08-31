-- 从项目根目录执行；只打开已有实验副本，文件不存在时直接报错。
.bail on
.timer off
.open 'file:data/practice/index_lab.db?mode=rw'
.timeout 5000
.headers off
.mode list

-- 准备阶段不计时：生成十万条数据，保留原来的商品和索引。
BEGIN;
WITH RECURSIVE numbers(n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM numbers WHERE n < 100000
)
INSERT OR IGNORE INTO items (id, name, price, category)
SELECT 1000000 + n, printf('INDEX-LAB-%06d', n), 19.99, 'index-lab'
FROM numbers;

-- 固定编号让重复执行不重复插入；若编号被其他数据占用，则停止并回滚。
CREATE TEMP TABLE seed_check (ok INTEGER NOT NULL CHECK (ok = 1));
INSERT INTO seed_check
SELECT COUNT(*) = 100000
FROM items
WHERE id BETWEEN 1000001 AND 1100000
  AND name = printf('INDEX-LAB-%06d', id - 1000000)
  AND price = 19.99
  AND category = 'index-lab';

CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
COMMIT;
SELECT 'total_rows', COUNT(*) FROM items;

-- 对比相同条件和返回字段，只改变是否允许普通索引参与查询。
-- 此处按名称查询，因此禁用普通索引后预期为全表扫描。
.print PLAN_A_SCAN
EXPLAIN QUERY PLAN
SELECT id, name, price FROM items NOT INDEXED
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print PLAN_B_NORMAL
EXPLAIN QUERY PLAN
SELECT id, name, price FROM items
WHERE name = 'INDEX-LAB-100000' ORDER BY id;

-- 各预热一次，不计时；同时检查两条查询是否返回相同商品。
.print WARMUP_A
SELECT id, name, price FROM items NOT INDEXED
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print WARMUP_B
SELECT id, name, price FROM items
WHERE name = 'INDEX-LAB-100000' ORDER BY id;

-- 交替计时五轮：看每条查询下方的 real，单位是秒。
-- 很短的查询可能显示零，不代表没有耗时；不要据此计算无限倍加速。
.timer on
.print A1_SCAN
SELECT id, name, price FROM items NOT INDEXED
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print B1_NORMAL
SELECT id, name, price FROM items
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print A2_SCAN
SELECT id, name, price FROM items NOT INDEXED
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print B2_NORMAL
SELECT id, name, price FROM items
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print A3_SCAN
SELECT id, name, price FROM items NOT INDEXED
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print B3_NORMAL
SELECT id, name, price FROM items
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print A4_SCAN
SELECT id, name, price FROM items NOT INDEXED
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print B4_NORMAL
SELECT id, name, price FROM items
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print A5_SCAN
SELECT id, name, price FROM items NOT INDEXED
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.print B5_NORMAL
SELECT id, name, price FROM items
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
.timer off
