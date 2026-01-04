import sqlite3

DB="foodbiz.db"

# (nombre, precio_mxn, categoria)
items = [
("Gelatina de rompope", 20, "GELATINAS"),
("Gelatina de cajeta", 20, "GELATINAS"),
("Gelatina de queso", 25, "GELATINAS"),
("Gelatina de jerez", 20, "GELATINAS"),
("Gelatina combinada", 20, "GELATINAS"),
("Gelatina de agua", 20, "GELATINAS"),
("Gelatina de mosaico", 35, "GELATINAS"),

("Ensalada de manzana", 50, "ANTOJO"),
("Fresas con crema", 50, "ANTOJO"),
("Arroz con leche", 30, "ANTOJO"),
("Flan napolitano (Ch)", 35, "ANTOJO"),
("Chocolatin", 40, "ANTOJO"),
("Chocolatito", 80, "ANTOJO"),
("Paycito de limón", 50, "ANTOJO"),
("Paycito de fresa", 50, "ANTOJO"),
("Paycito de queso", 40, "ANTOJO"),
("Rebanada de nevado", 65, "ANTOJO"),
("Raton", 40, "ANTOJO"),
("Mostachon CH", 70, "ANTOJO"),
("Tuti de queso", 20, "ANTOJO"),
("Estrudel de manzana", 95, "ANTOJO"),
("Merengues", 25, "ANTOJO"),
("Volovan de atun", 20, "ANTOJO"),
("Empanadas de jamon c queso", 20, "ANTOJO"),
("Galleta nuez c chocolate", 15, "ANTOJO"),
("Galleta nuez", 35, "ANTOJO"),
("Tamal de cazuela", 240, "ANTOJO"),
]

conn = sqlite3.connect(DB)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS menu_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  price_cents INTEGER NOT NULL,
  category TEXT NOT NULL
)
""")

added, updated = 0, 0

for name, price_mxn, cat in items:
    price_cents = int(round(float(price_mxn) * 100))
    # Si ya existe (por nombre), actualiza precio y categoría.
    cur.execute("SELECT id FROM menu_items WHERE TRIM(UPPER(name)) = TRIM(UPPER(?)) LIMIT 1", (name,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE menu_items SET price_cents=?, category=? WHERE id=?", (price_cents, cat, row[0]))
        updated += 1
    else:
        cur.execute("INSERT INTO menu_items (name, price_cents, category) VALUES (?,?,?)", (name, price_cents, cat))
        added += 1

conn.commit()
conn.close()

print(f"Import terminado. Agregados: {added}, Actualizados: {updated}")
