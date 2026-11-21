import argparse
import base64
import binascii
import hmac
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PRIORITIES = {"Low", "Medium", "High", "Urgent"}
SLA_HOURS = {"Urgent": (1, 4), "High": (4, 12), "Medium": (8, 24), "Low": (24, 72)}
TRANSITIONS = {
    "Open": {"In progress"},
    "In progress": {"Open", "Resolved"},
    "Resolved": {"In progress"},
}
ASSETS = {
    "/": ("index.html", "text/html"),
    "/style.css": ("style.css", "text/css"),
    "/app.js": ("app.js", "text/javascript"),
}


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize(path):
    with closing(connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                requester TEXT NOT NULL, priority TEXT NOT NULL, assignee TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Open', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
                message TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        db.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(tickets)")}
        for column in ("first_response_at", "resolved_at"):
            if column not in columns:
                db.execute(f"ALTER TABLE tickets ADD COLUMN {column} TEXT")
        db.execute("""
            UPDATE tickets SET first_response_at = (
                SELECT MIN(created_at) FROM activity
                WHERE ticket_id = tickets.id AND message LIKE 'Status changed from Open to In progress'
            ) WHERE first_response_at IS NULL
        """)
        db.execute("""
            UPDATE tickets SET resolved_at = (
                SELECT MAX(created_at) FROM activity
                WHERE ticket_id = tickets.id AND message LIKE 'Status changed from In progress to Resolved'
            ) WHERE status = 'Resolved' AND resolved_at IS NULL
        """)


def ticket_view(row, now=None):
    ticket = dict(row)
    now = now or datetime.now(timezone.utc)
    created = datetime.fromisoformat(ticket["created_at"]).replace(tzinfo=timezone.utc)
    deadlines = [created + timedelta(hours=hours) for hours in SLA_HOURS[ticket["priority"]]]
    milestones = []
    for label, deadline, timestamp in zip(
        ("Response", "Resolution"), deadlines,
        (ticket["first_response_at"], ticket["resolved_at"]),
    ):
        completed = datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc) if timestamp else None
        if completed:
            state = "Met" if completed <= deadline else "Breached"
        elif now >= deadline:
            state = "Breached"
        else:
            state = "Due soon" if deadline - now <= timedelta(hours=1) else "On track"
        milestones.append({"label": label, "due_at": deadline.isoformat(), "state": state,
                           "completed": completed is not None})
    ticket["sla"] = milestones
    return ticket


def field(data, name, limit, required=True):
    value = data.get(name, "")
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f"{name.capitalize()} must contain {'1' if required else '0'}–{limit} characters.")
    return value.strip()


def create_ticket(db, data):
    values = [field(data, name, limit) for name, limit in (
        ("title", 120), ("description", 2000), ("requester", 100), ("priority", 10)
    )]
    if values[-1] not in PRIORITIES:
        raise ValueError("Select a valid priority.")
    ticket_id = db.execute(
        "INSERT INTO tickets(title, description, requester, priority) VALUES (?, ?, ?, ?)", values
    ).lastrowid
    db.execute("INSERT INTO activity(ticket_id, message) VALUES (?, ?)", (ticket_id, "Ticket opened"))
    return ticket_id


def detail(db, ticket_id, requester=None):
    ticket = db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if ticket is None or (requester is not None and ticket["requester"] != requester):
        raise LookupError("Ticket not found.")
    events = db.execute(
        "SELECT message, created_at FROM activity WHERE ticket_id = ? ORDER BY id DESC", (ticket_id,)
    )
    return {**ticket_view(ticket), "activity": [
        dict(event) for event in events
        if requester is None or not event["message"].startswith("Note:")
    ]}


def update_ticket(db, ticket_id, data):
    if not data or set(data) - {"assignee", "status", "note", "reply"}:
        raise ValueError("Supply an assignee, status, note, or public reply.")
    db.execute("BEGIN IMMEDIATE")
    ticket = detail(db, ticket_id)
    status = field(data, "status", 20) if "status" in data else ticket["status"]
    assignee = field(data, "assignee", 100, required=False) if "assignee" in data else ticket["assignee"]
    note = field(data, "note", 1000, required=False)
    reply = field(data, "reply", 1000, required=False)
    events = []
    if status != ticket["status"]:
        if status not in TRANSITIONS[ticket["status"]]:
            raise ValueError("This status change is not allowed.")
        events.append(f"Status changed from {ticket['status']} to {status}")
    if assignee != ticket["assignee"]:
        events.append(f"Assigned to {assignee}" if assignee else "Assignment cleared")
    if note:
        events.append(f"Note: {note}")
    if reply:
        events.append(f"Public reply: {reply}")
    db.execute("""
        UPDATE tickets SET status = ?, assignee = ?,
            first_response_at = CASE WHEN ? = 'In progress'
                THEN COALESCE(first_response_at, CURRENT_TIMESTAMP) ELSE first_response_at END,
            resolved_at = CASE WHEN ? = 'Resolved' AND status <> 'Resolved' THEN CURRENT_TIMESTAMP
                WHEN ? <> 'Resolved' THEN NULL ELSE resolved_at END
        WHERE id = ?
    """, (status, assignee, status, status, status, ticket_id))
    db.executemany("INSERT INTO activity(ticket_id, message) VALUES (?, ?)",
                   ((ticket_id, event) for event in events))


def handler(database, demo=False):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, content, mime="application/json"):
            body = json.dumps(content).encode() if mime == "application/json" else content
            self.send_response(status)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Send an application/json request.")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                raise ValueError("Invalid request size.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            return data

        def identity(self):
            if not demo:
                return None
            if self.headers.get("X-Support-Desk-Demo") != "1":
                raise PermissionError("Sign in to the demo.")
            scheme, _, token = self.headers.get("Authorization", "").partition(" ")
            try:
                credentials = base64.b64decode(token, validate=True).decode("ascii") if scheme == "Basic" else ""
            except (binascii.Error, UnicodeError):
                credentials = ""
            accounts = {"analyst": None, "maya": "Maya Lin", "noah": "Noah Roy"}
            for user, requester in accounts.items():
                if hmac.compare_digest(credentials, f"{user}:desk-demo"):
                    return requester
            raise PermissionError("Use one of the demo accounts.")

        def handle_request(self):
            path = urlsplit(self.path).path
            if self.command == "GET" and path in ASSETS:
                name, mime = ASSETS[path]
                return self.send(200, (ROOT / "web" / name).read_bytes(), mime)
            try:
                requester = self.identity()
                if path == "/api/session" and self.command == "GET":
                    return self.send(200, {"demo": demo, "requester": requester})
                with closing(connect(database)) as db, db:
                    if path == "/api/tickets":
                        if self.command == "GET":
                            rows = db.execute(
                                "SELECT * FROM tickets WHERE ? IS NULL OR requester = ? ORDER BY id DESC",
                                (requester, requester),
                            )
                            now = datetime.now(timezone.utc)
                            return self.send(200, [ticket_view(row, now) for row in rows])
                        if self.command == "POST":
                            data = self.read_json()
                            if requester is not None:
                                data["requester"] = requester
                            ticket_id = create_ticket(db, data)
                            db.commit()
                            return self.send(201, detail(db, ticket_id, requester))
                    if path.startswith("/api/tickets/") and path.removeprefix("/api/tickets/").isascii():
                        suffix = path.removeprefix("/api/tickets/")
                        if suffix.isdecimal() and len(suffix) <= 18:
                            ticket_id = int(suffix)
                            if self.command == "PATCH":
                                if requester is not None:
                                    detail(db, ticket_id, requester)
                                    return self.send(403, {"error": "Only analysts can update tickets."})
                                update_ticket(db, ticket_id, self.read_json())
                                db.commit()
                            if self.command in {"GET", "PATCH"}:
                                return self.send(200, detail(db, ticket_id, requester))
                raise LookupError("Route not found.")
            except PermissionError as error:
                self.send(401, {"error": str(error)})
            except (ValueError, UnicodeError) as error:
                self.send(400, {"error": str(error)})
            except LookupError as error:
                self.send(404, {"error": str(error)})
            except sqlite3.Error:
                self.send(500, {"error": "Ticket database unavailable."})

        do_GET = handle_request
        do_POST = handle_request
        do_PATCH = handle_request

    return Handler


def seed_demo(path):
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM tickets LIMIT 1").fetchone():
            return
        for title, description, requester, priority, minutes in (
            ("VPN outage after password reset", "Remote access fails after a password change.", "Maya Lin", "Urgent", 35),
            ("Request access to the project folder", "New starter needs the team share.", "Noah Roy", "Low", 20),
            ("Shared printer unavailable", "The finance department cannot print on floor two.", "Maya Lin", "High", 850),
        ):
            ticket_id = create_ticket(db, dict(title=title, description=description, requester=requester,
                                              priority=priority))
            created = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute("UPDATE tickets SET created_at = ? WHERE id = ?", (created, ticket_id))
            db.execute("UPDATE activity SET created_at = ? WHERE ticket_id = ?", (created, ticket_id))
            if minutes == 850:
                db.execute("INSERT INTO activity(ticket_id, message) VALUES (?, ?)",
                           (ticket_id, "Note: Routed to site support for a queue check."))


def serve():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.demo and args.database is not None:
        parser.error("--demo uses its own fictional database and cannot be combined with --database")
    database = ROOT / "demo.sqlite3" if args.demo else args.database or ROOT / "tickets.sqlite3"
    initialize(database)
    if args.demo:
        seed_demo(database)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler(database, args.demo)) as server:
        print(f"Support Desk is running at http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    serve()
