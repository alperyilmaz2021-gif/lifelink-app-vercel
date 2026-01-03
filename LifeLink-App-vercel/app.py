import os, sqlite3
from flask import Flask, g, render_template, request, redirect, url_for, jsonify, abort
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
import socket

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IS_VERCEL = bool(os.environ.get('VERCEL') or os.environ.get('VERCEL_ENV') or os.environ.get('NOW_REGION'))

# On Vercel, the filesystem is read-only except /tmp. Use /tmp for the SQLite DB so the demo can write data.
DEFAULT_DB_PATH = '/tmp/lifelink.db' if IS_VERCEL else os.path.join(BASE_DIR, 'lifelink.db')
DB_PATH = os.environ.get('LIFELINK_DB', DEFAULT_DB_PATH)
PORT = int(os.environ.get("PORT", "5000"))
LOCAL_TZ = ZoneInfo(os.environ.get("LIFELINK_TZ", "America/Chicago"))

app = Flask(__name__)

# ---------- DB helpers ----------

def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH, check_same_thread=False)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


def seed_if_needed():
    """Create and seed the SQLite database if it doesn't exist.

    Notes:
      - On Vercel, DB_PATH defaults to /tmp/lifelink.db so writes are allowed.
      - Seeding uses seed.sql shipped with the repo (read-only is fine).
    """
    if not os.path.exists(DB_PATH):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if os.path.dirname(DB_PATH) else None
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        seed_path = os.path.join(BASE_DIR, 'seed.sql')
        with open(seed_path, 'r') as f:
            cur.executescript(f.read())
        conn.commit()
        conn.close()


seed_if_needed()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


@app.context_processor
def inject_now():
    return {"now": datetime.utcnow()}


# ---------- Time helpers ----------

def parse_ts(ts: Optional[str]):
    if not ts:
        return None
    # SQLite CURRENT_TIMESTAMP uses "YYYY-MM-DD HH:MM:SS"
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        # Fallback to fromisoformat
        dt = datetime.fromisoformat(ts)
    return dt.replace(tzinfo=ZoneInfo("UTC"))


def to_local_str(ts: Optional[str]):
    dt = parse_ts(ts)
    if not dt:
        return ""
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M %Z")


# ---------- Basic pages ----------

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


# ---------- Customer side: browse + place delivery request ----------

@app.route("/organ-listings")
def organ_listings():
    q = request.args.get("q", "").strip().lower()
    typ = request.args.get("type", "All")
    availability = request.args.get("availability", "All")

    sql = "SELECT * FROM organ_listings WHERE 1=1"
    args = []

    if typ and typ != "All":
        sql += " AND lower(organ_type) = ?"
        args.append(typ.lower())

    if availability == "Available":
        sql += " AND availability_status = 'Available'"
    elif availability == "Unavailable":
        sql += " AND availability_status = 'Unavailable'"

    if q:
        like = f"%{q}%"
        sql += (
            " AND ("
            "   lower(organ_type) LIKE ?"
            "   OR lower(blood_type) LIKE ?"
            "   OR lower(hospital_name) LIKE ?"
            "   OR lower(city) LIKE ?"
            "   OR lower(state) LIKE ?"
            ")"
        )
        args.extend([like, like, like, like, like])

    # Order by priority (Emergency > Critical > Urgent > Normal) then newest first
    sql += (
        " ORDER BY CASE priority_status "
        "   WHEN 'Emergency' THEN 1 "
        "   WHEN 'Critical' THEN 2 "
        "   WHEN 'Urgent' THEN 3 "
        "   ELSE 4 "
        " END, created_at DESC"
    )

    organs = query(sql, args)
    return render_template("organ_listings.html", organs=organs)


@app.route("/request-transport/<int:listing_id>", methods=["GET", "POST"])
def request_transport(listing_id: int):
    listing = query("SELECT * FROM organ_listings WHERE id = ?", (listing_id,), one=True)
    if not listing:
        abort(404, "Listing not found")

    # Enforce availability at request time
    if listing["availability_status"] != "Available":
        return render_template(
            "request_transport_unavailable.html",
            listing=listing,
        ), 400

    hospitals = query("SELECT * FROM hospitals ORDER BY name ASC")

    if request.method == "POST":
        d = request.form

        hospital_id = d.get("hospital_id")
        destination = d.get("destination", "").strip()
        contact_phone = d.get("contact_phone", "").strip()
        notes = d.get("notes", "").strip()

        if not hospital_id:
            return "Hospital is required", 400

        # Lookup requesting hospital and enforce that it exists
        hospital_row = query("SELECT * FROM hospitals WHERE id = ?", (hospital_id,), one=True)
        if not hospital_row:
            return "Selected hospital not found", 400

        if not destination or not contact_phone:
            return "Delivery address and contact phone are required", 400

        origin = f"{listing['hospital_name']} ({listing['city']}, {listing['state']})"

        order_id = execute(
            """
            INSERT INTO transport_requests(
                listing_id,hospital,organ_type,origin,destination,contact_phone,notes,
                priority_status,status,driver_id
            )
            VALUES(?,?,?,?,?,?,?,?,?,NULL)
            """,
            (
                listing["id"],
                hospital_row["name"],
                listing["organ_type"],
                origin,
                destination,
                contact_phone,
                notes,
                listing["priority_status"],
                "Requested",
            ),
        )

        # Immediately mark listing as unavailable
        execute(
            "UPDATE organ_listings SET availability_status = 'Unavailable' WHERE id = ?",
            (listing["id"],),
        )

        return redirect(url_for("order_confirmation", order_id=order_id))

    return render_template("request_transport.html", listing=listing, hospitals=hospitals)


# ---------- Order confirmation ----------

@app.route("/order-confirmation/<int:order_id>")
def order_confirmation(order_id: int):
    order = query(
        """
        SELECT tr.*,
               ol.organ_type AS listing_organ_type,
               ol.blood_type AS listing_blood_type,
               ol.hospital_name AS source_hospital,
               ol.city AS source_city,
               ol.state AS source_state,
               ol.priority_status AS listing_priority_status,
               d.first_name || ' ' || d.last_name AS driver_name,
               d.phone AS driver_phone
        FROM transport_requests tr
        LEFT JOIN organ_listings ol ON tr.listing_id = ol.id
        LEFT JOIN drivers d ON tr.driver_id = d.id
        WHERE tr.id = ?
        """,
        (order_id,),
        one=True,
    )
    if not order:
        abort(404, "Order not found")

    created_local = to_local_str(order["created_at"])
    updated_local = to_local_str(order["updated_at"])

    return render_template(
        "order_confirmation.html",
        order=order,
        created_local=created_local,
        updated_local=updated_local,
    )


# ---------- Hospital side: inbound / outbound views ----------

@app.route("/for_hospitals")
def for_hospitals():
    hospitals = query("SELECT * FROM hospitals ORDER BY name ASC")
    selected_id = request.args.get("hospital_id")
    selected_hospital = None

    if hospitals:
        if selected_id:
            selected_hospital = query(
                "SELECT * FROM hospitals WHERE id = ?",
                (selected_id,),
                one=True,
            )
        if not selected_hospital:
            selected_hospital = hospitals[0]

    outbound = []
    inbound = []

    if selected_hospital:
        hname = selected_hospital["name"]

        # Outbound: requests originating from this hospital
        outbound = query(
            """
            SELECT tr.*,
                   ol.organ_type AS listing_organ_type,
                   ol.blood_type AS listing_blood_type,
                   ol.hospital_name AS source_hospital,
                   ol.city AS source_city,
                   ol.state AS source_state
            FROM transport_requests tr
            LEFT JOIN organ_listings ol ON tr.listing_id = ol.id
            WHERE tr.hospital = ?
            ORDER BY tr.created_at DESC
            """,
            (hname,),
        )

        # Inbound: requests targeting organs owned by this hospital
        inbound = query(
            """
            SELECT tr.*,
                   ol.organ_type AS listing_organ_type,
                   ol.blood_type AS listing_blood_type,
                   ol.hospital_name AS source_hospital,
                   ol.city AS source_city,
                   ol.state AS source_state
            FROM transport_requests tr
            LEFT JOIN organ_listings ol ON tr.listing_id = ol.id
            WHERE ol.hospital_name = ?
            ORDER BY tr.created_at DESC
            """,
            (hname,),
        )

    listings = []
    if selected_hospital:
        listings = query(
            """
            SELECT * FROM organ_listings
            WHERE hospital_name = ?
            ORDER BY created_at DESC
            """,
            (selected_hospital["name"],),
        )

    return render_template(
        "for_hospitals.html",
        hospitals=hospitals,
        selected_hospital=selected_hospital,
        outbound=outbound,
        inbound=inbound,
        listings=listings,
    )


@app.route("/hospital-registration", methods=["GET", "POST"])
def hospital_registration():
    if request.method == "POST":
        d = request.form
        execute(
            "INSERT INTO hospitals(name,city,state,email) VALUES(?,?,?,?)",
            (d["name"], d["city"], d["state"], d["email"]),
        )
        return redirect(url_for("for_hospitals"))
    return render_template("hospital_registration.html")


@app.route("/hospital-login", methods=["GET", "POST"])
def hospital_login():
    # Placeholder: no auth for now, flows are hospital-selection based
    if request.method == "POST":
        return redirect(url_for("for_hospitals"))
    return render_template("hospital_login.html")


# Allow hospital users to upload new organs for their facility
@app.route("/new-listing", methods=["GET", "POST"])
def new_listing():
    hospitals = query("SELECT * FROM hospitals ORDER BY name ASC")
    if request.method == "POST":
        d = request.form
        hospital_id = d.get("hospital_id")
        if not hospital_id:
            return "Hospital selection is required", 400

        hrow = query("SELECT * FROM hospitals WHERE id = ?", (hospital_id,), one=True)
        if not hrow:
            return "Hospital not found", 400

        priority = d.get("priority_status", "Normal")
        availability = d.get("availability_status", "Available")

        execute(
            """
            INSERT INTO organ_listings(
                hospital_id,hospital_name,organ_type,blood_type,age,weight_kg,
                priority_status,availability_status,city,state
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                hrow["id"],
                hrow["name"],
                d["organ_type"],
                d["blood_type"],
                int(d["age"]),
                float(d["weight_kg"]),
                priority,
                availability,
                hrow["city"],
                hrow["state"],
            ),
        )
        return redirect(url_for("for_hospitals", hospital_id=hrow["id"]))

    return render_template("new_listing.html", hospitals=hospitals)


# ---------- Emergency transport (simple form) ----------

@app.route("/emergency-transport", methods=["GET", "POST"])
def emergency_transport():
    if request.method == "POST":
        d = request.form
        execute(
            """
            INSERT INTO transport_requests(
                listing_id,hospital,organ_type,origin,destination,contact_phone,notes,
                priority_status,status,driver_id
            )
            VALUES(NULL,?,?,?,?,?,?,?,'Emergency','Requested',NULL)
            """,
            (
                d["hospital"],
                d["organ_type"],
                d["origin"],
                d["destination"],
                d.get("contact_phone", ""),
                d.get("notes", ""),
            ),
        )
        return redirect(url_for("for_hospitals"))
    return render_template("emergency_transport.html")


@app.route("/demo-hospital")
def demo_hospital():
    return render_template("demo_hospital.html")


# ---------- Driver portal / admin for delivery ops ----------

def driver_has_active_order(driver_id: int) -> bool:
    row = query(
        """
        SELECT COUNT(*) AS c
        FROM transport_requests
        WHERE driver_id = ?
          AND status IN ('Assigned','En-route')
        """,
        (driver_id,),
        one=True,
    )
    return (row["c"] if row else 0) > 0


@app.route("/driver-portal")
def driver_portal():
    drivers = query("SELECT * FROM drivers ORDER BY first_name, last_name")
    selected_id = request.args.get("driver_id", type=int)
    selected_driver = None
    if drivers:
        if selected_id:
            selected_driver = query(
                "SELECT * FROM drivers WHERE id = ?", (selected_id,), one=True
            )
        if not selected_driver:
            selected_driver = drivers[0]

    current_order = None
    completed_orders = []
    available_orders = []

    if selected_driver:
        did = selected_driver["id"]
        current_order = query(
            """
            SELECT tr.*,
                   ol.organ_type AS listing_organ_type,
                   ol.blood_type AS listing_blood_type,
                   ol.hospital_name AS source_hospital
            FROM transport_requests tr
            LEFT JOIN organ_listings ol ON tr.listing_id = ol.id
            WHERE tr.driver_id = ?
              AND tr.status IN ('Assigned','En-route')
            ORDER BY tr.created_at DESC
            LIMIT 1
            """,
            (did,),
            one=True,
        )

        completed_orders = query(
            """
            SELECT tr.*,
                   ol.organ_type AS listing_organ_type,
                   ol.blood_type AS listing_blood_type,
                   ol.hospital_name AS source_hospital
            FROM transport_requests tr
            LEFT JOIN organ_listings ol ON tr.listing_id = ol.id
            WHERE tr.driver_id = ?
              AND tr.status = 'Delivered'
            ORDER BY tr.updated_at DESC
            LIMIT 20
            """,
            (did,),
        )

    # Available orders (unassigned, status Requested), sorted by priority then request time
    available_orders = query(
        """
        SELECT tr.*,
               ol.organ_type AS listing_organ_type,
               ol.blood_type AS listing_blood_type,
               ol.hospital_name AS source_hospital
        FROM transport_requests tr
        LEFT JOIN organ_listings ol ON tr.listing_id = ol.id
        WHERE tr.status = 'Requested'
          AND (tr.driver_id IS NULL)
        ORDER BY CASE tr.priority_status
                   WHEN 'Emergency' THEN 1
                   WHEN 'Urgent' THEN 2
                   WHEN 'Critical' THEN 3
                   ELSE 4
                 END,
                 tr.created_at ASC
        """
    )

    return render_template(
        "driver_portal.html",
        drivers=drivers,
        selected_driver=selected_driver,
        current_order=current_order,
        completed_orders=completed_orders,
        available_orders=available_orders,
        to_local_str=to_local_str,
    )


@app.route("/apply-driver", methods=["POST"])
def apply_driver():
    d = request.form
    # Store application for admin review
    execute(
        """
        INSERT INTO driver_applications(first_name,last_name,email,phone,cdl)
        VALUES(?,?,?,?,?)
        """,
        (d["first_name"], d["last_name"], d["email"], d["phone"], d["cdl"]),
    )
    # Also register as a driver so they appear in the dropdown immediately
    execute(
        """
        INSERT INTO drivers(first_name,last_name,email,phone,cdl)
        VALUES(?,?,?,?,?)
        """,
        (d["first_name"], d["last_name"], d["email"], d["phone"], d["cdl"]),
    )
    return redirect(url_for("driver_portal"))


@app.route("/driver-claim/<int:order_id>", methods=["POST"])
def driver_claim(order_id: int):
    driver_id = request.form.get("driver_id", type=int)
    if not driver_id:
        return "Driver required", 400

    if driver_has_active_order(driver_id):
        return "Driver already has an active order", 400

    order = query("SELECT * FROM transport_requests WHERE id = ?", (order_id,), one=True)
    if not order or order["status"] != "Requested":
        return "Order is no longer available", 400

    execute(
        """
        UPDATE transport_requests
        SET driver_id = ?, status = 'Assigned', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (driver_id, order_id),
    )
    return redirect(url_for("driver_portal", driver_id=driver_id))


@app.route("/driver-update-status/<int:order_id>", methods=["POST"])
def driver_update_status(order_id: int):
    driver_id = request.form.get("driver_id", type=int)
    new_status = request.form.get("status")

    if not driver_id or not new_status:
        return "Driver and status are required", 400

    order = query("SELECT * FROM transport_requests WHERE id = ?", (order_id,), one=True)
    if not order:
        return "Order not found", 404

    # Prevent changes once delivered
    if order["status"] == "Delivered":
        return "Delivered orders cannot be modified", 400

    # Only allow reverting Assigned -> Requested, or progressing along the flow
    allowed_statuses = {"Requested", "Assigned", "En-route", "Delivered"}
    if new_status not in allowed_statuses:
        return "Invalid status", 400

    # If reverting to Requested, clear driver assignment
    if new_status == "Requested":
        execute(
            """
            UPDATE transport_requests
            SET status = ?, driver_id = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_status, order_id),
        )
    else:
        execute(
            """
            UPDATE transport_requests
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_status, order_id),
        )

    return redirect(url_for("driver_portal", driver_id=driver_id))


# ---------- Simple reports placeholder (no metrics dashboard yet) ----------

@app.route("/reports")
def reports():
    return render_template("reports.html")



def _find_free_port(preferred):
    try_ports = list(range(preferred, preferred + 20))
    for p in try_ports:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("0.0.0.0", p))
            s.close()
            return p
        except OSError:
            continue
    # Let OS assign a random free port as a last resort
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.close()
    return port

# ---------- API ----------

@app.route("/api/organs")
def api_organs():
    rows = query("SELECT * FROM organ_listings ORDER BY created_at DESC")
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    chosen_port = _find_free_port(PORT)
    print(f"Starting LifeLink on :{chosen_port}")
    app.run(host="0.0.0.0", port=chosen_port, debug=False)
