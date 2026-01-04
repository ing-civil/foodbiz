from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from datetime import datetime
import os
import shutil

app = Flask(__name__)
app.secret_key = "foodbiz-secret"

DB_PATH = "foodbiz.db"
ADMIN_PIN = "1234"  # cámbialo


# -------------------------
# Utilidades DB
# -------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


@app.template_filter("pesos")
def pesos(cents: int):
    return f"${int(cents)/100:,.2f} MXN"


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


# -------------------------
# Inicialización DB
# -------------------------
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
        price_cents INTEGER NOT NULL CHECK(price_cents >= 0)
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

    # Seed mínimo: SOLO menú del día (para no duplicar cosas que tú agregas después)
    cur.execute("SELECT COUNT(*) FROM menu_items WHERE category='MENU'")
    if cur.fetchone()[0] == 0:
        cur.execute(
            "INSERT INTO menu_items (name, price_cents, category) VALUES (?,?,?)",
            ("Menú del día", 10000, "MENU")
        )
        conn.commit()

    conn.close()


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

    return {
        "total_qty": total_qty,
        "total_cents": total_cents,
        "menu_qty": menu_qty,
        "menu_cents": menu_cents,
        "other_qty": total_qty - menu_qty,
        "other_cents": total_cents - menu_cents,
    }


# -------------------------
# Rutas
# -------------------------
@app.route("/")
def index():
    conn = db()
    lc = last_cut_at(conn)
    totals = totals_since(conn, lc)

    cur = conn.cursor()
    cur.execute("""
      SELECT o.id, o.created_at, o.status,
             COALESCE(SUM(oi.qty * oi.price_cents),0) AS total_cents
      FROM orders o
      LEFT JOIN order_items oi ON oi.order_id=o.id
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

    # Limpieza para que "ANTOJO " o " ANTOJO" no te arruinen la vida:
    cur.execute("""
        SELECT id, name, price_cents, TRIM(UPPER(category)) AS category
        FROM menu_items
        WHERE TRIM(UPPER(category)) IN ('PASTELES','GELATINAS','ANTOJO')
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

    cur.execute("""
        SELECT id, name, price_cents, TRIM(UPPER(category)) AS category
        FROM menu_items
        WHERE TRIM(UPPER(category)) IN ('PASTELES','GELATINAS','ANTOJO')
        ORDER BY category, name
    """)
    items = cur.fetchall()

    if request.method == "POST":
        cur.execute("INSERT INTO orders (created_at, status) VALUES (?, 'ok')", (now_iso(),))
        order_id = cur.lastrowid

        added_lines = 0
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
                added_lines += 1

        if added_lines == 0:
            cur.execute("DELETE FROM orders WHERE id=?", (order_id,))
            conn.commit()
            conn.close()
            flash("No agregaste productos.", "error")
            return redirect(url_for("new_order"))

        conn.commit()
        conn.close()
        flash(f"Venta registrada (#{order_id}).", "ok")
        return redirect(url_for("index"))

    conn.close()
    grouped = {"PASTELES": [], "GELATINAS": [], "ANTOJO": []}
    for it in items:
        grouped[it["category"]].append(it)

    return render_template("new_order.html", grouped=grouped)


@app.route("/orders/menu", methods=["POST"])
def quick_menu_order():
    conn = db()
    cur = conn.cursor()

    cur.execute("INSERT INTO orders (created_at, status) VALUES (?, 'ok')", (now_iso(),))
    order_id = cur.lastrowid

    cur.execute("SELECT id, price_cents FROM menu_items WHERE category='MENU' LIMIT 1")
    menu = cur.fetchone()

    cur.execute("""
        INSERT INTO order_items (order_id, menu_item_id, qty, price_cents)
        VALUES (?,?,?,?)
    """, (order_id, menu["id"], 1, menu["price_cents"]))

    conn.commit()
    conn.close()
    flash("Menú registrado ($100).", "ok")
    return redirect(url_for("index"))


@app.route("/orders/<int:order_id>/cancel", methods=["POST"])
def cancel_order(order_id: int):
    reason = (request.form.get("reason") or "").strip()
    pin = (request.form.get("pin") or "").strip()

    if not require_pin(pin):
        flash("PIN incorrecto. No se canceló.", "error")
        return redirect(url_for("index"))

    if not reason:
        flash("Falta motivo.", "error")
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
    cur.execute(
        "INSERT INTO adjustments (created_at, kind, order_id, reason) VALUES (?,?,?,?)",
        (now_iso(), "cancel", order_id, reason)
    )

    conn.commit()
    conn.close()
    flash(f"Venta #{order_id} cancelada.", "ok")
    return redirect(url_for("index"))


@app.route("/sales")
def sales():
    conn = db()
    lc = last_cut_at(conn)
    totals = totals_since(conn, lc)

    cur = conn.cursor()
    cur.execute("SELECT * FROM cuts ORDER BY id DESC LIMIT 30")
    cuts = cur.fetchall()
    conn.close()

    return render_template("sales.html", last_cut=lc, totals=totals, cuts=cuts)


@app.route("/cuts/new", methods=["POST"])
def make_cut():
    pin = (request.form.get("pin") or "").strip()
    cashier = (request.form.get("cashier") or "").strip()
    counted = (request.form.get("counted") or "").strip()

    if not require_pin(pin):
        flash("PIN incorrecto.", "error")
        return redirect(url_for("sales"))

    if not cashier:
        flash("Pon responsable (nombre o iniciales).", "error")
        return redirect(url_for("sales"))

    try:
        counted_cents = int(round(float(counted) * 100))
    except Exception:
        flash("Efectivo contado inválido. Ej: 1520.50", "error")
        return redirect(url_for("sales"))

    conn = db()
    lc = last_cut_at(conn)
    t = totals_since(conn, lc)

    expected_cents = t["total_cents"]
    diff_cents = counted_cents - expected_cents

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO cuts (
          cut_at, cashier,
          expected_cents, counted_cents, diff_cents,
          total_qty, menu_qty, menu_cents, other_qty, other_cents
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        now_iso(), cashier,
        expected_cents, counted_cents, diff_cents,
        t["total_qty"], t["menu_qty"], t["menu_cents"], t["other_qty"], t["other_cents"]
    ))

    conn.commit()
    conn.close()

    create_backup()
    flash("Corte guardado y respaldo creado (backups/).", "ok")
    return redirect(url_for("sales"))


@app.route("/reset", methods=["POST"])
def reset_period():
    pin = (request.form.get("pin") or "").strip()
    if not require_pin(pin):
        flash("PIN incorrecto.", "error")
        return redirect(url_for("sales"))

    conn = db()
    lc = last_cut_at(conn)
    cur = conn.cursor()

    cur.execute("""
      DELETE FROM order_items
      WHERE order_id IN (SELECT id FROM orders WHERE created_at >= ? AND status='ok')
    """, (lc,))
    cur.execute("DELETE FROM orders WHERE created_at >= ? AND status='ok'", (lc,))

    conn.commit()
    conn.close()

    flash("Periodo actual reiniciado.", "ok")
    return redirect(url_for("sales"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
