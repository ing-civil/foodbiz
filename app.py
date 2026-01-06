from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from datetime import datetime, date
import os
import shutil

app = Flask(__name__)
app.secret_key = "foodbiz-secret"

DB_PATH = "foodbiz.db"

# NIP ADMIN (cámbialo cuando quieras)
ADMIN_PIN = "1234"

# Fondo fijo de caja (cambio) al inicio del día
CASH_FLOAT_CENTS = 46000  # $460.00


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


def safe_add_column(conn, table, col, coldef):
    # Agrega columna si no existe (SQLite)
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r["name"] for r in cur.fetchall()]
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coldef}")
        conn.commit()


def init_db():
    conn = db()
    cur = conn.cursor()

    # Productos
    cur.execute("""
    CREATE TABLE IF NOT EXISTS menu_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
      category TEXT NOT NULL
    )
    """)

    # Ventas
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

    # Cancelaciones / ajustes
    cur.execute("""
    CREATE TABLE IF NOT EXISTS adjustments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      kind TEXT NOT NULL,
      order_id INTEGER,
      reason TEXT NOT NULL
    )
    """)

    # Cortes
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cuts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      cut_at TEXT NOT NULL,
      cashier TEXT NOT NULL,
      cash_float_cents INTEGER NOT NULL,
      expected_cents INTEGER NOT NULL,
      counted_cents INTEGER NOT NULL,
      diff_cents INTEGER NOT NULL,
      total_qty INTEGER NOT NULL,
      total_cents INTEGER NOT NULL,
      expenses_cents INTEGER NOT NULL DEFAULT 0
    )
    """)

    # Relación / cierre del día
    cur.execute("""
    CREATE TABLE IF NOT EXISTS day_closings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      closed_at TEXT NOT NULL,
      closed_by TEXT NOT NULL,
      from_cut_at TEXT NOT NULL,
      cash_float_cents INTEGER NOT NULL,
      sales_total_cents INTEGER NOT NULL,
      sales_total_qty INTEGER NOT NULL,
      expenses_cents INTEGER NOT NULL,
      expected_cash_cents INTEGER NOT NULL,
      note TEXT NOT NULL DEFAULT ''
    )
    """)

    # Egresos (gas, huevo, etc.)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      concept TEXT NOT NULL,
      amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
      note TEXT NOT NULL DEFAULT ''
    )
    """)

    # Inventario: ubicaciones
    cur.execute("""
    CREATE TABLE IF NOT EXISTS locations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT NOT NULL UNIQUE,   -- 'CENTRO', 'PV'
      name TEXT NOT NULL
    )
    """)

    # Inventario: movimientos
    cur.execute("""
    CREATE TABLE IF NOT EXISTS inventory_moves (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      item_id INTEGER NOT NULL,
      qty INTEGER NOT NULL CHECK(qty > 0),
      from_location_id INTEGER,
      to_location_id INTEGER,
      kind TEXT NOT NULL,          -- TRANSFER, SALE, ADJUST_IN, ADJUST_OUT
      note TEXT NOT NULL DEFAULT ''
    )
    """)

    # Pedidos/encargos
    cur.execute("""
    CREATE TABLE IF NOT EXISTS special_orders (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      delivery_date TEXT NOT NULL,        -- YYYY-MM-DD
      customer_name TEXT NOT NULL,
      customer_phone TEXT NOT NULL,
      note_number TEXT NOT NULL,
      description TEXT NOT NULL,
      total_cents INTEGER NOT NULL,
      deposit_cents INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'open' -- open, delivered, cancelled
    )
    """)

    conn.commit()

    # Columnas opcionales (mínimos de inventario en PV)
    safe_add_column(conn, "menu_items", "min_pv_stock", "INTEGER NOT NULL DEFAULT 0")

    # Seed ubicaciones
    cur.execute("SELECT COUNT(*) AS n FROM locations")
    if cur.fetchone()["n"] == 0:
        cur.executemany(
            "INSERT INTO locations (code, name) VALUES (?,?)",
            [("CENTRO", "Centro de producción"), ("PV", "Punto de venta")]
        )
        conn.commit()

    # Seed productos si está vacío
    cur.execute("SELECT COUNT(*) AS n FROM menu_items")
    if cur.fetchone()["n"] == 0:
        seed = []

        # MENÚ (4 precios) + bebida
        seed += [
            ("Menú $80", 8000, "MENU"),
            ("Menú $90", 9000, "MENU"),
            ("Menú $95", 9500, "MENU"),
            ("Menú $100", 10000, "MENU"),
            ("Bebida", 3000, "MENU_EXTRA"),
            ("Purê de papa", 3000, "MENU_EXTRA"),
            ("Verduras", 3000, "MENU_EXTRA"),
            ("Spaguetti", 3000, "MENU_EXTRA"),
            ("Crema", 3000, "MENU_EXTRA"),
            ("Sopa de pasta", 2000, "MENU_EXTRA"),
            ("Arroz", 2000, "MENU_EXTRA"),
        ]

        # ANTOJITOS (los que pediste)
        antojitos = [
            ("Gelatina de rompope", 2000),
            ("Gelatina de cajeta", 2000),
            ("Gelatina de queso", 2500),
            ("Gelatina de jerez", 2000),
            ("Gelatina combinada", 2000),
            ("Gelatina de agua", 2000),
            ("Gelatina de mosaico", 3500),
            ("Ensalada de manzana", 5000),
            ("Fresas con crema", 5000),
            ("Arroz con leche", 3000),
            ("Flan napolitano (Ch)", 3500),
            ("Chocolatin", 4000),
            ("Chocolatito", 8000),
            ("Paycito de limón", 5000),
            ("Paycito de fresa", 5000),
            ("Paycito de queso", 4000),
            ("Rebanada de nevado", 6500),
            ("Raton", 4000),
            ("Mostachon CH", 7000),
            ("Tuti de queso", 2000),
            ("Estrudel de manzana", 9500),
            ("Merengues", 2500),
            ("Volovan de atun", 2000),
            ("Empanadas de jamon c queso", 2000),
            ("Galleta nuez c chocolate", 1500),
            ("Galleta nuez", 3500),
            ("Tamal de cazuela", 24000),
            ("Frijoles", 5500),
            ("Concha", 5000),
            ("Salsa macha", 6000),
            ("Rosca de reyes CH", 22000),
            ("Rosca de reyes GDE", 38000),
        ]
        for n, p in antojitos:
            seed.append((n, p, "ANTOJO"))

        cur.executemany(
            "INSERT INTO menu_items (name, price_cents, category) VALUES (?,?,?)",
            seed
        )
        conn.commit()

    conn.close()


def get_location_id(conn, code: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM locations WHERE code=?", (code,))
    r = cur.fetchone()
    if not r:
        raise RuntimeError("Ubicación no encontrada")
    return int(r["id"])


def last_cut_at(conn) -> str:
    cur = conn.cursor()
    cur.execute("SELECT cut_at FROM cuts ORDER BY id DESC LIMIT 1")
    r = cur.fetchone()
    return r["cut_at"] if r else "1970-01-01T00:00:00"


def sales_totals_since(conn, iso_dt: str):
    cur = conn.cursor()
    cur.execute("""
      SELECT COALESCE(SUM(oi.qty), 0) AS qty,
             COALESCE(SUM(oi.qty * oi.price_cents), 0) AS cents
      FROM orders o
      JOIN order_items oi ON oi.order_id = o.id
      WHERE o.status='ok' AND o.created_at >= ?
    """, (iso_dt,))
    r = cur.fetchone()
    return {"qty": int(r["qty"]), "cents": int(r["cents"])}


def expenses_since(conn, iso_dt: str) -> int:
    cur = conn.cursor()
    cur.execute("""
      SELECT COALESCE(SUM(amount_cents), 0) AS cents
      FROM expenses
      WHERE created_at >= ?
    """, (iso_dt,))
    r = cur.fetchone()
    return int(r["cents"])


def stock_for_location(conn, location_code: str):
    loc_id = get_location_id(conn, location_code)
    cur = conn.cursor()
    cur.execute("""
      SELECT mi.id, mi.name, mi.category, mi.price_cents, mi.min_pv_stock,
             COALESCE(SUM(CASE WHEN im.to_location_id = ? THEN im.qty ELSE 0 END), 0)
           - COALESCE(SUM(CASE WHEN im.from_location_id = ? THEN im.qty ELSE 0 END), 0) AS stock
      FROM menu_items mi
      LEFT JOIN inventory_moves im ON im.item_id = mi.id
      WHERE mi.category IN ('ANTOJO','PASTELES','GELATINAS','MENU','MENU_EXTRA')
      GROUP BY mi.id
      ORDER BY mi.category, mi.name
    """, (loc_id, loc_id))
    return cur.fetchall()


def grouped_items(conn, categories):
    cur = conn.cursor()
    q_marks = ",".join(["?"] * len(categories))
    cur.execute(f"""
      SELECT * FROM menu_items
      WHERE category IN ({q_marks})
      ORDER BY category, name
    """, tuple(categories))
    items = cur.fetchall()
    grouped = {c: [] for c in categories}
    for it in items:
        grouped[it["category"]].append(it)
    return grouped


@app.route("/")
def index():
    conn = db()
    lc = last_cut_at(conn)
    sales = sales_totals_since(conn, lc)
    exp = expenses_since(conn, lc)

    # últimas 10 ventas
    cur = conn.cursor()
    cur.execute("""
      SELECT o.id, o.created_at, o.status,
             COALESCE(SUM(oi.qty * oi.price_cents), 0) AS total_cents
      FROM orders o
      LEFT JOIN order_items oi ON oi.order_id=o.id
      GROUP BY o.id
      ORDER BY o.id DESC
      LIMIT 10
    """)
    recent = cur.fetchall()
    conn.close()

    return render_template("index.html", last_cut=lc, sales=sales, expenses_cents=exp, recent_orders=recent)


@app.route("/menu")
def menu():
    conn = db()
    grouped = grouped_items(conn, ["MENU", "MENU_EXTRA", "ANTOJO", "PASTELES", "GELATINAS"])
    conn.close()
    return render_template("menu.html", grouped=grouped)


@app.route("/orders/new", methods=["GET", "POST"])
def new_order():
    conn = db()
    grouped = grouped_items(conn, ["MENU", "MENU_EXTRA", "ANTOJO", "PASTELES", "GELATINAS"])

    # Lista plana para iterar en POST
    all_items = []
    for cat in grouped:
        all_items += grouped[cat]

    if request.method == "POST":
        cur = conn.cursor()
        cur.execute("INSERT INTO orders (created_at, status) VALUES (?, 'ok')", (now_iso(),))
        order_id = cur.lastrowid

        added = 0
        for it in all_items:
            raw = request.form.get(f"qty_{it['id']}", "0").strip()
            try:
                qty = int(raw)
            except Exception:
                qty = 0

            if qty > 0:
                cur.execute("""
                  INSERT INTO order_items (order_id, menu_item_id, qty, price_cents)
                  VALUES (?,?,?,?)
                """, (order_id, it["id"], qty, it["price_cents"]))
                added += 1

                # Inventario: salida del PV por venta (SALE)
                pv_id = get_location_id(conn, "PV")
                cur.execute("""
                  INSERT INTO inventory_moves (created_at, item_id, qty, from_location_id, to_location_id, kind, note)
                  VALUES (?,?,?,?,?,?,?)
                """, (now_iso(), it["id"], qty, pv_id, None, "SALE", f"Venta #{order_id}"))

        if added == 0:
            cur.execute("DELETE FROM orders WHERE id=?", (order_id,))
            conn.commit()
            conn.close()
            flash("No agregaste productos.", "error")
            return redirect(url_for("new_order"))

        conn.commit()
        conn.close()
        flash(f"Venta registrada (#{order_id}).", "ok")
        return redirect(url_for("ticket", order_id=order_id))

    conn.close()
    return render_template("new_order.html", grouped=grouped)


@app.route("/orders/<int:order_id>/cancel", methods=["POST"])
def cancel_order(order_id: int):
    reason = (request.form.get("reason") or "").strip()
    pin = (request.form.get("pin") or "").strip()

    if not require_pin(pin):
        flash("NIP incorrecto. No se canceló.", "error")
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

    cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    order = cur.fetchone()
    if not order:
        conn.close()
        flash("Venta no encontrada.", "error")
        return redirect(url_for("index"))

    cur.execute("""
      SELECT mi.name, oi.qty, oi.price_cents, (oi.qty * oi.price_cents) AS line_cents
      FROM order_items oi
      JOIN menu_items mi ON mi.id = oi.menu_item_id
      WHERE oi.order_id=?
      ORDER BY mi.category, mi.name
    """, (order_id,))
    items = cur.fetchall()

    total_cents = sum(int(r["line_cents"]) for r in items)
    conn.close()

    return render_template("ticket.html", order=order, items=items, total_cents=total_cents)


@app.route("/expenses", methods=["GET", "POST"])
def expenses():
    conn = db()
    if request.method == "POST":
        concept = (request.form.get("concept") or "").strip()
        amount = (request.form.get("amount") or "").strip()
        note = (request.form.get("note") or "").strip()

        if not concept:
            flash("Falta concepto.", "error")
            conn.close()
            return redirect(url_for("expenses"))

        try:
            amount_cents = int(round(float(amount) * 100))
        except Exception:
            flash("Monto inválido. Usa formato 123.50", "error")
            conn.close()
            return redirect(url_for("expenses"))

        cur = conn.cursor()
        cur.execute("""
          INSERT INTO expenses (created_at, concept, amount_cents, note)
          VALUES (?,?,?,?)
        """, (now_iso(), concept, amount_cents, note))
        conn.commit()
        conn.close()
        flash("Egreso registrado.", "ok")
        return redirect(url_for("expenses"))

    cur = conn.cursor()
    cur.execute("SELECT * FROM expenses ORDER BY id DESC LIMIT 50")
    rows = cur.fetchall()
    conn.close()
    return render_template("expenses.html", rows=rows)


@app.route("/sales")
def sales():
    conn = db()
    lc = last_cut_at(conn)

    sales = sales_totals_since(conn, lc)
    exp = expenses_since(conn, lc)

    # efectivo esperado en caja = fondo + ventas - egresos
    expected_cash = CASH_FLOAT_CENTS + sales["cents"] - exp

    cur = conn.cursor()
    cur.execute("SELECT * FROM cuts ORDER BY id DESC LIMIT 20")
    cuts = cur.fetchall()

    cur.execute("SELECT * FROM day_closings ORDER BY id DESC LIMIT 20")
    closings = cur.fetchall()

    conn.close()

    return render_template(
        "sales.html",
        last_cut=lc,
        cash_float_cents=CASH_FLOAT_CENTS,
        sales=sales,
        expenses_cents=exp,
        expected_cash_cents=expected_cash,
        cuts=cuts,
        closings=closings
    )


@app.route("/cuts/new", methods=["POST"])
def make_cut():
    pin = (request.form.get("pin") or "").strip()
    cashier = (request.form.get("cashier") or "").strip()
    counted = (request.form.get("counted") or "").strip()

    if not require_pin(pin):
        flash("NIP incorrecto. No se hizo corte.", "error")
        return redirect(url_for("sales"))

    if not cashier:
        flash("Pon quién hizo el corte.", "error")
        return redirect(url_for("sales"))

    try:
        counted_cents = int(round(float(counted) * 100))
    except Exception:
        flash("Efectivo contado inválido. Usa 1234.50", "error")
        return redirect(url_for("sales"))

    conn = db()
    lc = last_cut_at(conn)

    s = sales_totals_since(conn, lc)
    exp = expenses_since(conn, lc)
    expected_cash = CASH_FLOAT_CENTS + s["cents"] - exp
    diff = counted_cents - expected_cash

    cur = conn.cursor()
    cur.execute("""
      INSERT INTO cuts (
        cut_at, cashier,
        cash_float_cents,
        expected_cents, counted_cents, diff_cents,
        total_qty, total_cents,
        expenses_cents
      ) VALUES (?,?,?,?,?,?,?,?,?)
    """, (now_iso(), cashier, CASH_FLOAT_CENTS, expected_cash, counted_cents, diff, s["qty"], s["cents"], exp))

    conn.commit()
    conn.close()
    create_backup()

    flash("Corte guardado y respaldo creado.", "ok")
    return redirect(url_for("sales"))


@app.route("/closing/new", methods=["POST"])
def make_closing():
    pin = (request.form.get("pin") or "").strip()
    who = (request.form.get("who") or "").strip()
    note = (request.form.get("note") or "").strip()

    if not require_pin(pin):
        flash("NIP incorrecto. No se generó la relación.", "error")
        return redirect(url_for("sales"))

    if not who:
        flash("Pon quién hizo la relación.", "error")
        return redirect(url_for("sales"))

    conn = db()
    lc = last_cut_at(conn)
    s = sales_totals_since(conn, lc)
    exp = expenses_since(conn, lc)

    expected_cash = CASH_FLOAT_CENTS + s["cents"] - exp

    cur = conn.cursor()
    cur.execute("""
      INSERT INTO day_closings (
        closed_at, closed_by, from_cut_at,
        cash_float_cents,
        sales_total_cents, sales_total_qty,
        expenses_cents,
        expected_cash_cents,
        note
      ) VALUES (?,?,?,?,?,?,?,?,?)
    """, (now_iso(), who, lc, CASH_FLOAT_CENTS, s["cents"], s["qty"], exp, expected_cash, note))

    conn.commit()
    conn.close()

    flash("Relación guardada.", "ok")
    return redirect(url_for("sales"))


@app.route("/inventory")
def inventory_home():
    conn = db()
    pv = stock_for_location(conn, "PV")
    centro = stock_for_location(conn, "CENTRO")
    conn.close()

    # Agrupar por categoría para mostrar bonito
    def group(rows):
        g = {}
        for r in rows:
            g.setdefault(r["category"], []).append(r)
        return g

    return render_template("inventory.html", pv=group(pv), centro=group(centro))


@app.route("/inventory/ship", methods=["GET", "POST"])
def inventory_ship():
    conn = db()
    centro_id = get_location_id(conn, "CENTRO")
    pv_id = get_location_id(conn, "PV")

    items = grouped_items(conn, ["MENU", "MENU_EXTRA", "ANTOJO", "PASTELES", "GELATINAS"])
    all_items = []
    for c in items:
        all_items += items[c]

    if request.method == "POST":
        note = (request.form.get("note") or "").strip()
        cur = conn.cursor()
        moved = 0

        for it in all_items:
            raw = request.form.get(f"qty_{it['id']}", "0").strip()
            try:
                qty = int(raw)
            except Exception:
                qty = 0

            if qty > 0:
                cur.execute("""
                  INSERT INTO inventory_moves (created_at, item_id, qty, from_location_id, to_location_id, kind, note)
                  VALUES (?,?,?,?,?,?,?)
                """, (now_iso(), it["id"], qty, centro_id, pv_id, "TRANSFER", note))
                moved += 1

        if moved == 0:
            conn.close()
            flash("No agregaste cantidades para enviar.", "error")
            return redirect(url_for("inventory_ship"))

        conn.commit()
        conn.close()
        flash("Envío registrado (Centro → Punto de venta).", "ok")
        return redirect(url_for("inventory_home"))

    conn.close()
    return render_template("inventory_ship.html", grouped=items)


@app.route("/inventory/mins", methods=["POST"])
def inventory_set_mins():
    # Ajusta mínimos de stock en PV (sin NIP, porque es configuración; si quieres NIP lo hacemos)
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM menu_items")
    ids = [r["id"] for r in cur.fetchall()]

    for iid in ids:
        raw = (request.form.get(f"min_{iid}") or "").strip()
        if raw == "":
            continue
        try:
            v = int(raw)
        except Exception:
            v = 0
        if v < 0:
            v = 0
        cur.execute("UPDATE menu_items SET min_pv_stock=? WHERE id=?", (v, iid))

    conn.commit()
    conn.close()
    flash("Mínimos actualizados.", "ok")
    return redirect(url_for("inventory_home"))


@app.route("/pedidos")
def pedidos():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
      SELECT * FROM special_orders
      ORDER BY (status='open') DESC, delivery_date ASC, id DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return render_template("pedidos.html", pedidos=rows)


@app.route("/pedidos/new", methods=["GET", "POST"])
def pedidos_new():
    if request.method == "POST":
        delivery_date = (request.form.get("delivery_date") or "").strip()
        customer_name = (request.form.get("customer_name") or "").strip()
        customer_phone = (request.form.get("customer_phone") or "").strip()
        note_number = (request.form.get("note_number") or "").strip()
        description = (request.form.get("description") or "").strip()
        total = (request.form.get("total") or "").strip()
        deposit = (request.form.get("deposit") or "0").strip()

        if not (delivery_date and customer_name and customer_phone and note_number and description and total):
            flash("Faltan datos del pedido.", "error")
            return redirect(url_for("pedidos_new"))

        try:
            total_cents = int(round(float(total) * 100))
            deposit_cents = int(round(float(deposit) * 100))
        except Exception:
            flash("Total/anticipo inválido. Usa 1234.50", "error")
            return redirect(url_for("pedidos_new"))

        if deposit_cents < 0 or total_cents <= 0 or deposit_cents > total_cents:
            flash("Revisa total y anticipo.", "error")
            return redirect(url_for("pedidos_new"))

        conn = db()
        cur = conn.cursor()
        cur.execute("""
          INSERT INTO special_orders
          (created_at, delivery_date, customer_name, customer_phone, note_number, description, total_cents, deposit_cents, status)
          VALUES (?,?,?,?,?,?,?,?, 'open')
        """, (now_iso(), delivery_date, customer_name, customer_phone, note_number, description, total_cents, deposit_cents))

        conn.commit()
        conn.close()
        flash("Pedido registrado.", "ok")
        return redirect(url_for("pedidos"))

    return render_template("pedidos_new.html")


@app.route("/pedidos/<int:pid>/status", methods=["POST"])
def pedidos_status(pid: int):
    status = (request.form.get("status") or "").strip()
    pin = (request.form.get("pin") or "").strip()

    if status not in ("open", "delivered", "cancelled"):
        flash("Estado inválido.", "error")
        return redirect(url_for("pedidos"))

    # Para cancelar, pide NIP
    if status == "cancelled" and not require_pin(pin):
        flash("NIP incorrecto. No se canceló el pedido.", "error")
        return redirect(url_for("pedidos"))

    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE special_orders SET status=? WHERE id=?", (status, pid))
    conn.commit()
    conn.close()

    flash("Pedido actualizado.", "ok")
    return redirect(url_for("pedidos"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
