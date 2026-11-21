(() => {
  const accounts = new Map([["analyst", null], ["maya", "Maya Lin"], ["noah", "Noah Roy"]]);
  const hours = { Urgent: [1, 4], High: [4, 12], Medium: [8, 24], Low: [24, 72] };
  const transitions = { Open: ["In progress"], "In progress": ["Open", "Resolved"], Resolved: ["In progress"] };
  const hour = 3600000;
  let tickets;
  let sequence;

  function add(title, description, requester, priority, created = Date.now()) {
    const ticket = {
      id: ++sequence, title, description, requester, priority, created_at: created,
      status: "Open", assignee: "", first_response_at: null, resolved_at: null, activity: [],
    };
    record(ticket, "Ticket opened", created);
    tickets.push(ticket);
    return ticket;
  }

  function record(ticket, message, now = Date.now(), internal = false) {
    ticket.activity.unshift({ message, internal, created_at: new Date(now).toISOString().slice(0, 19).replace("T", " ") });
  }

  function reset() {
    tickets = [];
    sequence = 0;
    const now = Date.now();
    add("VPN outage after password reset", "Remote access fails after a password change.", "Maya Lin", "Urgent", now - 35 * hour / 60);
    add("Request access to the project folder", "New starter needs the team share.", "Noah Roy", "Low", now - 20 * hour / 60);
    const printer = add("Shared printer unavailable", "Finance cannot print on floor two.", "Maya Lin", "High", now - 14 * hour);
    record(printer, "Note: Routed to site support for a queue check.", now - 13 * hour, true);
  }

  function view(ticket, requester, includeActivity = false) {
    const { activity, ...result } = ticket;
    result.sla = ["Response", "Resolution"].map((label, index) => {
      const due = ticket.created_at + hours[ticket.priority][index] * hour;
      const completion = index ? ticket.resolved_at : ticket.first_response_at;
      const now = Date.now();
      let state = "On track";
      if (completion !== null) state = completion <= due ? "Met" : "Breached";
      else if (now >= due) state = "Breached";
      else if (due - now <= hour) state = "Due soon";
      return { label, due_at: new Date(due).toISOString(), state, completed: completion !== null };
    });
    if (includeActivity) {
      result.activity = activity.filter((event) => !requester || !event.internal)
        .map(({ message, created_at }) => ({ message, created_at }));
    }
    return result;
  }

  function fail(message, status) {
    throw Object.assign(new Error(message), { status });
  }

  function field(data, key, max, required = false) {
    const value = data[key] ?? "";
    if (typeof value !== "string" || value.trim().length > max || (required && !value.trim())) {
      fail("Check the " + key + " field.", 400);
    }
    return value.trim();
  }

  async function request(account, path, method, data = {}) {
    if (!accounts.has(account)) fail("Choose a demo account.", 401);
    const requester = accounts.get(account);
    if (path === "/session" && method === "GET") return { demo: true, requester };
    if (path === "/tickets") {
      if (method === "GET") {
        return tickets.filter((ticket) => !requester || ticket.requester === requester)
          .slice().reverse().map((ticket) => view(ticket, requester));
      }
      if (method === "POST") {
        const priority = field(data, "priority", 10, true);
        if (!Object.hasOwn(hours, priority)) fail("Select a valid priority.", 400);
        const ticket = add(field(data, "title", 120, true), field(data, "description", 2000, true),
          requester || field(data, "requester", 100, true), priority);
        return view(ticket, requester, true);
      }
    }
    const match = /^\/tickets\/(\d+)$/.exec(path);
    const ticket = match && tickets.find((item) => item.id === Number(match[1]));
    if (!ticket || (requester && ticket.requester !== requester)) fail("Ticket not found.", 404);
    if (method === "PATCH") {
      if (requester) fail("Only analysts can update tickets.", 403);
      const status = data.status === undefined ? ticket.status : field(data, "status", 20, true);
      if (status !== ticket.status && !transitions[ticket.status].includes(status)) fail("This status change is not allowed.", 400);
      const assignee = data.assignee === undefined ? ticket.assignee : field(data, "assignee", 100);
      const note = field(data, "note", 1000);
      const reply = field(data, "reply", 1000);
      const now = Date.now();
      if (status !== ticket.status) {
        record(ticket, "Status changed from " + ticket.status + " to " + status, now);
        if (status === "In progress") ticket.first_response_at ??= now;
        ticket.resolved_at = status === "Resolved" ? now : null;
        ticket.status = status;
      }
      if (assignee !== ticket.assignee) {
        record(ticket, assignee ? "Assigned to " + assignee : "Assignment cleared", now);
        ticket.assignee = assignee;
      }
      if (note) record(ticket, "Note: " + note, now, true);
      if (reply) record(ticket, "Public reply: " + reply, now);
    } else if (method !== "GET") {
      fail("Route not found.", 404);
    }
    return view(ticket, requester, true);
  }

  reset();
  globalThis.supportDeskDemo = { request, reset };
  document.querySelector("#reset-demo").addEventListener("click", () => {
    reset();
    document.dispatchEvent(new Event("desk-demo-reset"));
  });
})();
