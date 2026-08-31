"""模拟订单创建的第二步失败时，应用如何回滚整个事务。"""

from pathlib import Path
import sqlite3


DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "practice" / "index_lab.db"
ORDER_ID = 2001


def count_lab_order(connection: sqlite3.Connection) -> int:
    """只统计本次实验固定编号的订单，避免影响其他练习数据。"""
    row = connection.execute(
        "SELECT COUNT(*) FROM orders WHERE id = ?", (ORDER_ID,)
    ).fetchone()
    return int(row[0])


def main() -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        # SQLite 默认可能不检查外键；本实验主动开启，确保坏明细会失败。
        connection.execute("PRAGMA foreign_keys = ON")
        print("事务前订单数：", count_lab_order(connection))

        try:
            connection.execute("BEGIN")
            connection.execute(
                """
                INSERT INTO orders (id, user_id, status, coupon_code, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ORDER_ID, 1, "pending", None, "2026-08-31"),
            )
            print("第一步成功：订单已写入事务，尚未提交")

            # item_id 不存在，外键校验会抛出 IntegrityError。
            connection.execute(
                "INSERT INTO order_items (order_id, item_id, quantity) VALUES (?, ?, ?)",
                (ORDER_ID, 999999, 1),
            )
            connection.commit()
        except sqlite3.IntegrityError as error:
            print("第二步失败：", error)
            connection.rollback()
            print("已执行回滚")

        print("事务后订单数：", count_lab_order(connection))


if __name__ == "__main__":
    main()
