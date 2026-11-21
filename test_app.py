import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

from datetime import datetime, timezone

from app import handler, initialize, seed_demo, ticket_view


class TicketWorkflowTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.database = Path(tmp.name) / "test.sqlite3"
        initialize(self.database)

    def request(self, method, path, data=None, expected=200, *, demo=False, account=None, headers=None):
        request_type = handler(self.database, demo)
        request = request_type.__new__(request_type)
        request.command = method
        request.path = path
        body = json.dumps(data).encode() if data is not None else b""
        request.rfile = io.BytesIO(body)
        request.headers = type("Headers", (dict,), {
            "get_content_type": lambda self: "application/json"
        })({"Content-Length": str(len(body))})
        request.headers.update(headers or {})
        if account:
            token = base64.b64encode(f"{account}:desk-demo".encode()).decode()
            request.headers.update({"Authorization": f"Basic {token}", "X-Support-Desk-Demo": "1"})
        responses = []
        request.send = lambda status, content, *_: responses.append((status, content))
        request.handle_request()
        self.assertEqual(len(responses), 1)
        status, content = responses[0]
        self.assertEqual(status, expected, content)
        return content

    def test_ticket_lifecycle(self):
        self.assertEqual(self.request("GET", "/api/tickets"), [])
        self.request("POST", "/api/tickets", {"title": ""}, 400)
        ticket = self.request("POST", "/api/tickets", {
            "title": "VPN unavailable", "description": "Demo request",
            "requester": "Demo Employee", "priority": "High"
        }, 201)
        path = f"/api/tickets/{ticket['id']}"
        self.request("PATCH", path, {"status": "Resolved", "note": "must roll back"}, 400)
        self.assertEqual(len(self.request("GET", path)["activity"]), 1)
        updated = self.request("PATCH", path, {
            "status": "In progress", "assignee": "Demo Analyst", "note": "Checked settings"
        })
        self.assertEqual(updated["assignee"], "Demo Analyst")
        self.assertIsNotNone(updated["first_response_at"])
        self.assertEqual(len(updated["activity"]), 4)
        resolved = self.request("PATCH", path, {"status": "Resolved"})
        self.assertIsNotNone(resolved["resolved_at"])
        reopened = self.request("PATCH", path, {"status": "In progress"})
        self.assertIsNone(reopened["resolved_at"])
        self.assertEqual(reopened["first_response_at"], updated["first_response_at"])
        self.assertEqual(reopened["sla"][1]["due_at"], resolved["sla"][1]["due_at"])
        self.assertEqual(self.request("GET", "/api/tickets")[0]["id"], ticket["id"])
        self.assertEqual(len(self.request("GET", path)["activity"]), 6)
        self.request("GET", "/api/tickets/999", expected=404)

    def test_reject_cross_origin_and_rebinding(self):
        self.request("GET", "/api/tickets", expected=401,
                     headers={"Host": "attacker.example"})
        self.request("POST", "/api/tickets", {}, expected=401,
                     headers={"Host": "localhost:8091", "Origin": "https://attacker.example"})
        self.assertEqual(self.request("GET", "/api/tickets", headers={
            "Host": "localhost:8091", "Origin": "http://localhost:8091"}), [])

    def test_request_framing(self):
        for headers in ({"Transfer-Encoding": "chunked"}, {"Content-Length": "20000"},
                        {"Content-Length": "100"}, {"Content-Length": "invalid"}):
            with self.subTest(headers=headers):
                self.request("POST", "/api/tickets", {}, expected=400, headers=headers)
        self.assertEqual(self.request("GET", "/api/tickets"), [])

    def test_static_assets(self):
        self.assertIn(b"Ticket queue", self.request("GET", "/"))
        self.assertIn(b"renderDetail", self.request("GET", "/app.js"))
        self.assertIn(b".toolbar", self.request("GET", "/style.css"))

    def test_sla_boundaries(self):
        ticket = {
            "created_at": "2026-11-21 12:00:00", "priority": "Urgent",
            "first_response_at": None, "resolved_at": None,
        }
        now = datetime(2026, 11, 21, 12, 30, tzinfo=timezone.utc)
        self.assertEqual([s["state"] for s in ticket_view(ticket, now)["sla"]], ["Due soon", "On track"])
        now = now.replace(hour=13, minute=0)
        self.assertEqual(ticket_view(ticket, now)["sla"][0]["state"], "Breached")
        ticket["first_response_at"] = "2026-11-21 13:00:00"
        self.assertEqual(ticket_view(ticket, now)["sla"][0]["state"], "Met")

    def test_demo_access_and_public_replies(self):
        seed_demo(self.database)
        seed_demo(self.database)
        self.request("GET", "/api/tickets", demo=True, expected=401)
        analyst = {"demo": True, "account": "analyst"}
        maya = {"demo": True, "account": "maya"}
        noah = {"demo": True, "account": "noah"}
        self.assertEqual(len(self.request("GET", "/api/tickets", **analyst)), 3)
        own = self.request("GET", "/api/tickets", **maya)
        self.assertEqual(len(own), 2)
        ticket = own[0]
        path = f"/api/tickets/{ticket['id']}"
        self.request("GET", path, expected=404, **noah)
        self.request("PATCH", path, {"status": "In progress"}, expected=403, **maya)
        self.request("PATCH", path, {"note": "Private triage", "reply": "Working on it"}, **analyst)
        messages = [event["message"] for event in self.request("GET", path, **maya)["activity"]]
        self.assertIn("Public reply: Working on it", messages)
        self.assertFalse(any(message.startswith("Note:") for message in messages))
        created = self.request("POST", "/api/tickets", {
            "title": "Request", "description": "Example", "priority": "Low", "requester": "Noah Roy",
        }, expected=201, **maya)
        self.assertEqual(created["requester"], "Maya Lin")


if __name__ == "__main__":
    unittest.main()

