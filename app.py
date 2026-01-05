from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from datetime import datetime
import os
import shutil

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "foodbiz-secret")

DB_PATH = os.environ.get("DB_PATH", "foodbiz.db")
ADMIN_PIN = os.environ.get("ADMIN_PIN", "1234")


# -----------------------------
# DB helpers
# -----------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def require_pin(pin: str) -> bool:
    return (pin or "").strip() == ADMIN_PIN


def ensure_backup_folder():
    folder = os.path.join(os.getcwd(), "backups")
    os.makedirs(folder, exist_ok=True)
    return folder


def create_backup():
    if not os.path.exists(DB_PATH):
        return None
    folder = ensure_backup_folder()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(folder, f"foodbiz_{stamp}.db")
    shutil.copy2(DB_PATH, dst)
    return dst


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS menu_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
        category TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ok'
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        menu_item_id INTEGER NOT NULL,
        qty INTEGER NOT NULL CHECK(qty > 0),
        price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
        FOREIGN KEY(order_id) REFERENCES orders(id),
        FOREIGN KEY(menu_item_id) REFERENCES menu_items(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cuts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cut_at TEXT NOT NULL,
        cashier TEXT NOT NULL,
        expected_cents INTEGER NOT NULL,
        counted_cents INTEGER NOT NULL,
        diff_cents INTEGER NOT NULL,
        total_qty INTEGER NOT NULL,
        menu_qty INTEGER NOT NULL,
        menu_cents INTEGER NOT NULL,
        other_qty INTEGER NOT NULL,
        other_cents INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        kind TEXT NOT NULL,
        order_id INTEGER,
        reason TEXT NOT NULL
    )
    """)

    conn.commit()

    # Seed inicial
    cur.execute("SELECT COUNT(*) AS n FROM menu_items")
    if int(cur.fetchone()["n"]) == 0:
        seed = []

        # MENU del día (SIEMPRE EXISTE)
        seed.append(("Menú del día", 10000, "MENU"))

        # ANTOJO (tu tabla)
        seed += [
            ("Gelatina de rompope", 2000, "ANTOJO"),
            ("Gelatina de cajeta", 2000, "ANTOJO"),
            ("Gelatina de queso", 2500, "ANTOJO"),
            ("Gelatina de jerez", 2000, "ANTOJO"),
            ("Gelatina combinada", 2000, "ANTOJO"),
            ("Gelatina de agua", 2000, "ANTOJO"),
            ("Gelatina de mosaico", 3500, "ANTOJO"),
            ("Ensalada de manzana", 5000, "ANTOJO"),
            ("Fresas con crema", 5000, "ANTOJO"),
            ("Arroz con leche", 3000, "ANTOJO"),
            ("Flan napolitano (Ch)", 3500, "ANTOJO"),
            ("Chocolatin", 4000, "ANTOJO"),
            ("Chocolatito", 8000, "ANTOJO"),
            ("Paycito de limón", 5000, "ANTOJO"),
            ("Paycito de fresa", 5000, "ANTOJO"),
            ("Paycito de queso", 4000, "ANTOJO"),
            ("Rebanada de nevado", 6500, "ANTOJO"),
            ("Raton", 4000, "ANTOJO"),
            ("Mostachon CH", 7000, "ANTOJO"),
            ("Tuti de queso", 2000, "ANTOJO"),
            ("Estrudel de manzana", 9500, "ANTOJO"),
            ("Merengues", 2500, "ANTOJO"),
            ("Volovan de atun", 2000, "ANTOJO"),
            ("Empanadas de jamon c queso", 2000, "ANTOJO"),
            ("Galleta nuez c chocolate", 1500, "ANTOJO"),
            ("Galleta nuez", 3500, "ANTOJO"),
            ("Tamal de cazuela", 24000, "ANTOJO"),
        ]

        # PASTELES
        seed += [
            ("Nevado de coco (12)", 59000, "PASTELES"),
            ("Nevado de coco (20)", 76000, "PASTELES"),
            ("Tres leches de cajeta (12)", 59000, "PASTELES"),
            ("Tres leches de cajeta (20)", 76000, "PASTELES"),
            ("Cheese Cake (20)", 69000, "PASTELES"),
            ("Pastel de frutas navideño (20)", 66000, "PASTELES"),
            ("Nuez con cajeta (12)", 46000, "PASTELES"),
            ("Rosca de naranja (16)", 31000, "PASTELES"),
            ("Rosca de naranja envinada (16)", 33000, "PASTELES"),
            ("Rosca de nuez (16)", 31000, "PASTELES"),
            ("Tronco Navideño (20)", 42000, "PASTELES"),
            ("Niño envuelto (20)", 32000, "PASTELES"),
        ]

        # GELATINAS (grandes)
        seed += [
            ("Gelatina de cajeta (20)", 34000, "GELATINAS"),
            ("Gelatina de fresa (20)", 34000, "GELATINAS"),
            ("Gelatina de durazno (20)", 36000, "GELATINAS"),
            ("Gelatina de vino con rompope (20)", 22500, "GELATINAS"),
            ("Gelatina de vino con rompope (40)", 50000, "GELATINAS"),
            ("Gelatina de queso (20)", 34000, "GELATINAS"),
            ("Gelatina de mosaico (20)", 22500, "GELATINAS"),
        ]

        cur.executemany(
            "INSERT INTO menu_items (name, price_cents, category) VALUES (?,?,?)",
            seed
        )
        conn.commit()

    conn.close()


# -----------------------------
# Jinja filter
# -----------------------------
@app.template_filter("pesos")
def pesos(cents: int):
    return f"${int(cents)/100:,.2f} MXN"


# -----------------------------
# Totales por periodo
# -----------------------------
def last_cut_at(conn) -> str:
    cur = conn.cursor()
    cur.execute("SELECT cut_at FROM cuts ORDER BY id DESC LIMIT 1")
    r = cur.fetchone()
    return r["cut_at"] if r else "1970-01-01T00:00:00"


def totals_since(conn, iso_dt: str):
    cur = conn.cursor()

    cur.execute("""
        SELECT
          COALESCE(SUM(oi.qty), 0) AS total_qty,
          COALESCE(SUM(oi.qty * oi.price_cents), 0) AS total_cents
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        WHERE o.status='ok' AND o.created_at >= ?
    """, (iso_dt,))
    t = cur.fetchone()
    total_qty = int(t["total_qty"])
    total_cents = int(t["total_cents"])

    cur.execute("""
        SELECT
          COALESCE(SUM(oi.qty), 0) AS menu_qty,
          COALESCE(SUM(oi.qty * oi.price_cents), 0) AS menu_cents
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN menu_items mi ON mi.id = oi.menu_item_id
        WHERE o.status='ok' AND o.created_at >= ? AND mi.category='MENU'
    """, (iso_dt,))
    m = cur.fetchone()
    menu_qty = int(m["menu_qty"])
    menu_cents = int(m["menu_cents"])

    other_qty = total_qty - menu_qty
    other_cents = total_cents - menu_cents

    return {
        "total_qty": total_qty,
        "total_cents": total_cents,
        "menu_qty": menu_qty,
        "menu_cents": menu_cents,
        "other_qty": other_qty,
        "other_cents": other_cents,
    }


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def index():
    conn = db()
    lc = last_cut_at(conn)
    totals = totals_since(conn, lc)

    cur = conn.cursor()
    cur.execute("""
        SELECT o.id, o.created_at, o.status,
               COALESCE(SUM(oi.qty * oi.price_cents), 0) AS total_cents
        FROM orders o
        LEFT JOIN order_items oi ON oi.order_id = o.id
        GROUP BY o.id
        ORDER BY o.id DESC
        LIMIT 10
    """)
    recent_orders = cur.fetchall()
    conn.close()

    return render_template("index.html", last_cut=lc, totals=totals, recent_orders=recent_orders)


@app.route("/menu")
def menu():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM menu_items
        WHERE category IN ('PASTELES','GELATINAS','ANTOJO')
        ORDER BY category, name
    """)
    items = cur.fetchall()
    conn.close()

    grouped = {"PASTELES": [], "GELATINAS": [], "ANTOJO": []}
    for it in items:
        grouped[it["category"]].append(it)

    return render_template("menu.html", grouped=grouped)


@app.route("/orders/new", methods=["GET", "POST"])
def new_order():
    conn = db()
    cur = conn.cursor()

    # Incluye MENU dentro del registro de ventas y lo pone arriba
    cur.execute("""
        SELECT * FROM menu_items
        WHERE category IN ('MENU','PASTELES','GELATINAS','ANTOJO')
        ORDER BY
          CASE category
            WHEN 'MENU' THEN 0
            WHEN 'ANTOJO' THEN 1
            WHEN 'GELATINAS' THEN 2
            WHEN 'PASTELES' THEN 3
            ELSE 9
          END,
          name
    """)
    items = cur.fetchall()

    if request.method == "POST":
        cur.execute("INSERT INTO orders (created_at, status) VALUES (?, 'ok')", (now_iso(),))
        order_id = cur.lastrowid

        added_any = False
        for it in items:
            raw = request.form.get(f"qty_{it['id']}", "0")
            try:
                qty = int(raw)
            except Exception:
                qty = 0

            if qty > 0:
                cur.execute("""
                    INSERT INTO order_items (order_id, menu_item_id, qty, price_cents)
                    VALUES (?,?,?,?)
                """, (order_id, it["id"], qty, it["price_cents"]))
                added_any = True

        if not added_any:
            cur.execute("DELETE FROM orders WHERE id = ?", (order_id,))
            conn.commit()
            conn.close()
            flash("No agregaste productos.", "error")
            return redirect(url_for("new_order"))

        conn.commit()
        conn.close()
        flash(f"Venta registrada (#{order_id}).", "ok")
        return redirect(url_for("ticket", order_id=order_id))

    conn.close()

    grouped = {"MENU": [], "ANTOJO": [], "GELATINAS": [], "PASTELES": []}
    for it in items:
        grouped[it["category"]].append(it)

    return render_template("new_order.html", grouped=grouped)


@app.route("/orders/<int:order_id>/cancel", methods=["POST"])
def cancel_order(order_id: int):
    reason = (request.form.get("reason") or "").strip()
    pin = (request.form.get("pin") or "").strip()

    if not require_pin(pin):
        flash("PIN incorrecto. No se canceló.", "error")
        return redirect(url_for("index"))

    if not reason:
        flash("Falta motivo de cancelación.", "error")
        return redirect(url_for("index"))

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT status FROM orders WHERE id=?", (order_id,))
    o = cur.fetchone()
    if not o:
        conn.close()
        flash("Venta no encontrada.", "error")
        return redirect(url_for("index"))

    if o["status"] == "cancelled":
        conn.close()
        flash("Esa venta ya estaba cancelada.", "error")
        return redirect(url_for("index"))

    cur.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
    cur.execute("""
        INSERT INTO adjustments (created_at, kind, order_id, reason)
        VALUES (?,?,?,?)
    """, (now_iso(), "cancel", order_id, reason))

    conn.commit()
    conn.close()

    flash(f"Venta #{order_id} cancelada.", "ok")
    return redirect(url_for("index"))


@app.route("/ticket/<int:order_id>")
def ticket(order_id: int):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id, created_at, status FROM orders WHERE id = ?", (order_id,))
    order = cur.fetchone()
    if not order:
        conn.close()
        return "Ticket no encontrado", 404

    cur.execute("""
        SELECT
            mi.name AS name,
            mi.category AS category,
            oi.qty AS qty,
            oi.price_cents AS price_cents
        FROM order_items oi
        JOIN menu_items mi ON mi.id = oi.menu_item_id
        WHERE oi.order_id = ?
        ORDER BY mi.category, mi.name
    """, (order_id,))
    items = cur.fetchall()

    total_cents = sum(int(r["qty"]) * int(r["price_cents"]) for r in items)

    conn.close()
    return render_template("ticket.html", order=order, items=items, total_cents=total_cents)


@app.route("/sales")
def sales():
    conn = db()
    lc = last_cut_at(conn)
    totals = totals_since(conn, lc)

    cur = conn.cursor()
    cur.execute("SELECT * FROM cuts ORDER BY id DESC LIMIT 20")
    cuts = cur.fetchall()

    conn.close()
    return render_template("sales.html", last_cut=lc, totals=totals, cuts=cuts)


@app.route("/cuts/new", methods=["POST"])
def make_cut():
    pin = (request.form.get("pin") or "").strip()
    cashier = (request.form.get("cashier") or "").strip()
    counted = (request.form.get("counted") or "").strip()

    if not require_pin(pin):
        flash("PIN incorrecto. No se hizo corte.", "error")
        return redirect(url_for("sales"))

    if not cashier:
        flash("Pon quién hizo el corte (nombre o iniciales).", "error")
        return redirect(url_for("sales"))

    try:
        counted_cents = int(round(float(counted) * 100))
    except Exception:
        flash("Efectivo contado inválido. Usa formato 1234.50", "error")
        return redirect(url_for("sales"))

    conn = db()
    lc = last_cut_at(conn)
    t = totals_since(conn, lc)

    expected_cents = t["total_cents"]
    diff_cents = counted_cents - expected_cents
    cut_at = now_iso()

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO cuts (
            cut_at, cashier, expected_cents, counted_cents, diff_cents,
            total_qty, menu_qty, menu_cents, other_qty, other_cents
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        cut_at, cashier, expected_cents, counted_cents, diff_cents,
        t["total_qty"], t["menu_qty"], t["menu_cents"], t["other_qty"], t["other_cents"]
    ))

    conn.commit()
    conn.close()

    create_backup()
    flash("Corte guardado y respaldo creado.", "ok")
    return redirect(url_for("sales"))


@app.route("/reset", methods=["POST"])
def reset_period():
    pin = (request.form.get("pin") or "").strip()
    if not require_pin(pin):
        flash("PIN incorrecto. No se reinició.", "error")
        return redirect(url_for("sales"))

    conn = db()
    lc = last_cut_at(conn)
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM order_items
        WHERE order_id IN (
            SELECT id FROM orders WHERE created_at >= ? AND status='ok'
        )
    """, (lc,))
    cur.execute("DELETE FROM orders WHERE created_at >= ? AND status='ok'", (lc,))

    conn.commit()
    conn.close()

    flash("Periodo actual reiniciado (ventas ok borradas).", "ok")
    return redirect(url_for("sales"))


@app.route("/backup", methods=["POST"])
def manual_backup():
    pin = (request.form.get("pin") or "").strip()
    if not require_pin(pin):
        flash("PIN incorrecto. No se respaldó.", "error")
        return redirect(url_for("sales"))

    path = create_backup()
    if not path:
        flash("No se encontró la base de datos para respaldar.", "error")
        return redirect(url_for("sales"))

    flash("Respaldo creado en la carpeta /backups.", "ok")
    return redirect(url_for("sales"))


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
