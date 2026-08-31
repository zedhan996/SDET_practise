"""重建独立的SQL练习库；会清空练习表，不操作应用库或自动化测试库。"""

from pathlib import Path
import sqlite3


DATABASE_PATH = Path(__file__).resolve().parent / "data" / "practice" / "sql_practice.db"


def main() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS order_items;
            DROP TABLE IF EXISTS orders;
            DROP TABLE IF EXISTS items;
            DROP TABLE IF EXISTS users;

            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                city TEXT
            );

            CREATE TABLE items (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                category TEXT NOT NULL
            );

            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                coupon_code TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE order_items (
                order_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                PRIMARY KEY (order_id, item_id),
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (item_id) REFERENCES items(id)
            );

            INSERT INTO users (id, username, city) VALUES
                (1, 'alice', 'Shanghai'),
                (2, 'bob', 'Beijing'),
                (3, 'carol', NULL);

            INSERT INTO items (id, name, price, category) VALUES
                (101, 'iPhone 13', 3999.00, 'phone'),
                (102, 'Keyboard', 299.00, 'accessory'),
                (103, 'Monitor', 1299.00, 'display'),
                (104, 'USB Cable', 39.00, 'accessory');

            INSERT INTO orders
                (id, user_id, status, coupon_code, created_at)
            VALUES
                (1001, 1, 'paid', NULL, '2026-08-12'),
                (1002, 1, 'paid', 'SAVE10', '2026-08-13'),
                (1003, 2, 'pending', NULL, '2026-08-14'),
                (1004, 3, 'cancelled', NULL, '2026-08-15');

            INSERT INTO order_items (order_id, item_id, quantity) VALUES
                (1001, 101, 1),
                (1001, 102, 1),
                (1002, 103, 1),
                (1003, 102, 2),
                (1004, 101, 1);
            """
        )

        table_names = ("users", "items", "orders", "order_items")
        counts = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in table_names
        }

    print(f"Created: {DATABASE_PATH}")
    for table, count in counts.items():
        print(f"{table}: {count} rows")


if __name__ == "__main__":
    main()
