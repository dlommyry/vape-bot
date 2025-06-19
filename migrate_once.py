"""
Создаёт (или мигрирует) базу vape_shop.db к новой схеме:
products, flavors, cart, waitlist, orders.
Запускается ОДИН раз вручную, бот потом ничего не трогает.
"""
import sqlite3, pathlib, logging

DB = "vape_shop.db"
logging.basicConfig(level=logging.INFO)
con = sqlite3.connect(DB)
cur = con.cursor()

# базовые таблицы (id -> вкус -> остаток)
cur.executescript("""
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT,
    description TEXT
);
CREATE TABLE IF NOT EXISTS flavors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    flavor TEXT,
    qty INTEGER
);
CREATE TABLE IF NOT EXISTS cart (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    flavor_id INTEGER,
    qty INTEGER
);
CREATE TABLE IF NOT EXISTS waitlist (
    user_id INTEGER,
    flavor_id INTEGER
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    items TEXT,
    ts TEXT,
    status TEXT
);
""")
con.commit()

# миграция из старой схемы (products.quantity, products.flavors TEXT)
try:
    cur.execute("PRAGMA table_info(products_old)");
    # если products_old уже переименована, значит миграция была
    logging.info("База уже в новой схеме — миграция не нужна.")
except sqlite3.OperationalError:
    try:
        cur.execute("SELECT id,name,description,quantity,flavors FROM products")
        rows = cur.fetchall()
        if rows:
            logging.info("Найдена старая схема — переносим данные…")
            cur.executescript("DELETE FROM flavors;")
            for pid,name,desc,qty,flv in rows:
                if flv:
                    parts = [p.strip() for p in flv.split(",")]
                    per = qty // len(parts) if qty else 0
                    for f in parts:
                        cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",
                                    (pid,f,per))
                else:
                    cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",
                                (pid,"default",qty))
            cur.execute("ALTER TABLE products RENAME TO products_old;")
            cur.execute("CREATE TABLE products (id INTEGER PRIMARY KEY,name TEXT,description TEXT);")
            cur.executemany("INSERT INTO products(id,name,description) VALUES(?,?,?)",
                            [(pid,n,d) for pid,n,d,_,_ in rows])
            con.commit()
            logging.info("Миграция выполнена успешно.")
    except sqlite3.OperationalError:
        pass  # таблица products ещё не создана → ничего переносить

con.close()
logging.info("Готово. Файл %s сохранён.", DB)
