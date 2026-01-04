import sqlite3

conn = sqlite3.connect("foodbiz.db")
cur = conn.cursor()

cur.execute("SELECT COUNT(id) FROM menu_items WHERE category='ANTOJO'")
print("ANTOJOS:", cur.fetchone()[0])

cur.execute("SELECT name, price_cents FROM menu_items WHERE category='ANTOJO' LIMIT 5")
for row in cur.fetchall():
    print(row)

conn.close()
