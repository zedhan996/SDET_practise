# SQL 索引、事务与锁实验记录

## 实验范围

实验仅使用 `data/practice/index_lab.db`，这是从 SQL 练习库创建的副本。
不操作开发库、pytest 隔离库或应用接口。

## 1. 名称精确查询与索引

在 `items(name)` 上创建 `idx_items_name` 后，对精确名称查询进行对照：

```sql
SELECT id, name, price FROM items
WHERE name = 'INDEX-LAB-100000' ORDER BY id;
```

| 组别 | 查询方式 | 执行计划 | 五轮结果 |
| --- | --- | --- | --- |
| A | 加 `NOT INDEXED` | `SCAN items` | 约 16～18 ms/次 |
| B | 正常查询 | `SEARCH items USING INDEX idx_items_name` | CLI 显示 `0.000` 秒 |

两组均返回 `1100000|INDEX-LAB-100000|19.99`，因此本次比较保持了结果一致。

结论：索引在本实验的精确名称查询中被实际采用，查询路径由扫描表变为索引查找。
`0.000` 只是命令行计时精度下的显示，不代表真实耗时为零，也不能据此计算性能倍数。

`LIKE '%Keyboard%'` 的包含搜索仍显示 `SCAN items`；不能为使用索引而擅自把业务的包含搜索改成精确搜索。

## 2. 事务回滚

`sql_practice/transaction_basics.sql` 在同一个事务内插入两条带 `transaction-lab` 标记的数据。

| 时点 | 查询结果 |
| --- | --- |
| 事务开始前 | 0 条 |
| 事务内 | 2 条 |
| 执行 `ROLLBACK` 后 | 0 条 |

结论：未提交的数据可以在当前事务内被查询到，但显式回滚会撤销该事务中的写入。

`sql_practice/transaction_failure_demo.py` 进一步模拟订单创建：订单写入成功后，
订单明细引用不存在的商品，触发 `FOREIGN KEY constraint failed`。程序捕获
`sqlite3.IntegrityError` 并调用 `rollback()`，最终订单数仍为 0。

测试设计含义：多步骤写入要同时断言错误响应和数据库状态，避免留下半成品订单。

## 3. 写锁等待

连接 A 执行：

```sql
BEGIN IMMEDIATE;
UPDATE items SET price = price WHERE id = 102;
```

连接 B 设置 `.timeout 3000` 后执行同样的写入，实际等待约 3.340 秒并返回：

```text
Runtime error: database is locked (5)
```

连接 A 执行 `ROLLBACK` 并退出后，连接 B 重试相同写入，约 0.001 秒成功。

结论：SQLite 同一时刻只允许一个写事务。后续写入会等待；超过连接设置的等待时间后返回锁错误。持锁方提交或回滚后，锁被释放，后续写入可以继续。

排查时不能只看到接口慢或报错就认定根因，应结合请求时间线、数据库连接状态、锁等待时长、应用错误日志和事务边界判断。
