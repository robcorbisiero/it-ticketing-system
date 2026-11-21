const $ = (selector) => document.querySelector(selector);
const statuses = ["Open", "In progress", "Resolved"];
const transitions = {
  Open: ["In progress"],
  "In progress": ["Open", "Resolved"],
  Resolved: ["In progress"],
};
let tickets = [];
let selectedId;
let demoAccount = "";
let session = null;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function ticketNumber(id) {
  return "IT-" + String(id).padStart(4, "0");
}

function nextDeadline(ticket) {
  return ticket.sla.find((item) => !item.completed);
}

function deadlineTime(ticket) {
  const next = nextDeadline(ticket);
  return next ? Date.parse(next.due_at) : Infinity;
}

function slaBadge(state) {
  return element("span", state, "pill " + state.toLowerCase().replaceAll(" ", "-"));
}

async function api(path, method = "GET", data) {
  if (globalThis.supportDeskDemo) return globalThis.supportDeskDemo.request(demoAccount, path, method, data);
  const headers = {};
  if (demoAccount) {
    headers.Authorization = "Basic " + btoa(demoAccount + ":desk-demo");
    headers["X-Support-Desk-Demo"] = "1";
  }
  if (data !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch("/api" + path, { method, headers, body: JSON.stringify(data) });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.error || "The request failed."), { status: response.status });
  return result;
}

const request = (path, method, data) => api("/tickets" + path, method, data);

async function startSession() {
  session = await api("/session");
  $("#viewer").textContent = session.demo ? session.requester || "Analyst" : "";
  $("#sign-out").hidden = !session.demo;
  $("#analyst-controls").hidden = Boolean(session.requester);
  $("#requester-field").hidden = Boolean(session.requester);
  $("#create-form").elements.requester.disabled = Boolean(session.requester);
  $("#new-ticket").disabled = false;
  $("#message").textContent = "";
}

function render() {
  $("#counts").replaceChildren(...statuses.map((status) => {
    const card = element("button", undefined, "count");
    const active = $("#filter").value === status;
    card.type = "button";
    card.setAttribute("aria-pressed", String(active));
    const label = element("span", status, "count-label");
    label.prepend(element("i", undefined, "indicator " + status.toLowerCase().replaceAll(" ", "-")));
    const number = tickets.filter((ticket) => ticket.status === status).length;
    card.append(label, element("strong", number));
    card.addEventListener("click", () => {
      $("#filter").value = active ? "All statuses" : status;
      render();
      $("#counts").children[statuses.indexOf(status)].focus();
    });
    return card;
  }));
  const filter = $("#filter").value;
  const query = $("#search").value.trim().toLowerCase();
  const filtered = tickets.filter((ticket) =>
    (filter === "All statuses" || ticket.status === filter) &&
    [ticketNumber(ticket.id), ticket.title, ticket.requester].some((value) => value.toLowerCase().includes(query))
  );
  if ($("#sort").value === "deadline") {
    filtered.sort((a, b) => {
      const left = deadlineTime(a);
      const right = deadlineTime(b);
      return left === right ? b.id - a.id : left - right;
    });
  }
  $("#result-count").textContent = filtered.length + (filtered.length === 1 ? " ticket" : " tickets");
  $("#rows").replaceChildren(...filtered.map((ticket) => {
    const row = element("tr");
    const link = element("button", undefined, "ticket-link");
    link.append(element("span", ticketNumber(ticket.id), "ticket-id"), element("span", ticket.title, "ticket-title"));
    link.addEventListener("click", () => openTicket(ticket.id));
    const cell = element("td");
    cell.append(link);
    row.append(cell, element("td", ticket.requester));
    for (const value of [ticket.priority, ticket.status]) {
      const badge = element("td");
      badge.append(element("span", value, "pill " + value.toLowerCase().replaceAll(" ", "-")));
      row.append(badge);
    }
    row.append(element("td", ticket.assignee || "Unassigned"));
    const breached = ticket.sla.some((item) => item.state === "Breached");
    const sla = element("td");
    const next = nextDeadline(ticket);
    sla.append(slaBadge(breached ? "Breached" : next?.state ?? "Met"));
    if (next) {
      const date = new Date(next.due_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
      sla.append(element("small", next.label + " · " + date, "sla-time"));
    }
    row.append(sla);
    return row;
  }));
  $("#empty").hidden = filtered.length > 0;
  $("#empty").textContent = tickets.length ? "No tickets match these filters." : "No tickets yet. Create one to get started.";
}

async function refresh() {
  const activeSession = session;
  try {
    const result = await request("");
    if (session !== activeSession) return;
    tickets = result;
    render();
  } catch (error) {
    if (session === activeSession) $("#message").textContent = "Could not load the ticket queue. " + error.message;
  }
}

function renderDetail(ticket) {
  selectedId = ticket.id;
  $("#detail-title").textContent = ticketNumber(ticket.id) + " · " + ticket.title;
  $("#ticket-meta").textContent = ticket.requester + " · " + ticket.priority + " priority";
  $("#description").textContent = ticket.description;
  $("#deadlines").replaceChildren(...ticket.sla.map((item) => {
    const line = element("li");
    line.append(element("strong", item.label), slaBadge(item.state),
      element("time", new Date(item.due_at).toLocaleString()));
    line.querySelector("time").dateTime = item.due_at;
    return line;
  }));
  const form = $("#detail-form");
  form.elements.assignee.value = ticket.assignee;
  form.elements.note.value = "";
  form.elements.status.replaceChildren(
    ...[ticket.status, ...transitions[ticket.status]].map((status) => element("option", status))
  );
  $("#activity").replaceChildren(...ticket.activity.map((event) => {
    const entry = element("li", event.message);
    entry.append(element("small", event.created_at + " UTC"));
    return entry;
  }));
}

async function openTicket(id) {
  const activeSession = session;
  try {
    const ticket = await request("/" + id);
    if (session !== activeSession) return;
    renderDetail(ticket);
    $("#detail-form .error").textContent = "";
    if (!$("#detail-dialog").open) $("#detail-dialog").showModal();
  } catch (error) {
    if (session === activeSession) $("#message").textContent = error.message;
  }
}

function onSubmit(form, submit) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    if (button.disabled) return;
    button.disabled = true;
    const errorText = form.querySelector(".error");
    errorText.textContent = "";
    try {
      await submit(Object.fromEntries(new FormData(form)));
      await refresh();
    } catch (error) {
      errorText.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

$("#new-ticket").addEventListener("click", () => {
  $("#create-form .error").textContent = "";
  $("#create-dialog").showModal();
});
$("#login-dialog").addEventListener("cancel", (event) => event.preventDefault());
$("#sign-out").addEventListener("click", () => {
  demoAccount = "";
  session = null;
  selectedId = undefined;
  $("#detail-form").reset();
  $("#detail-title").textContent = "";
  $("#description").textContent = "";
  $("#activity").replaceChildren();
  $("#create-form").reset();
  $("#viewer").textContent = "";
  $("#message").textContent = "";
  $("#search").value = "";
  $("#filter").selectedIndex = 0;
  $("#sign-out").hidden = true;
  $("#new-ticket").disabled = true;
  tickets = [];
  render();
  $("#login-dialog").showModal();
});
for (const button of document.querySelectorAll("[data-close]")) {
  button.addEventListener("click", () => button.closest("dialog").close());
}
$("#search").addEventListener("input", render);
$("#filter").addEventListener("change", render);
$("#sort").addEventListener("change", render);
document.addEventListener("desk-demo-reset", () => {
  $("#search").value = "";
  $("#filter").selectedIndex = 0;
  $("#sort").value = "deadline";
  $("#create-form").reset();
  $("#detail-form").reset();
  $("#message").textContent = "Demo reset. The three sample tickets are restored.";
  if (session) refresh();
});
onSubmit($("#create-form"), async (data) => {
  const created = await request("", "POST", data);
  $("#create-form").reset();
  $("#create-dialog").close();
  $("#message").textContent = ticketNumber(created.id) + " was created.";
});
onSubmit($("#detail-form"), async (data) => {
  if (session?.requester) return;
  renderDetail(await request("/" + selectedId, "PATCH", data));
  $("#message").textContent = ticketNumber(selectedId) + " was updated.";
});
onSubmit($("#login-form"), async ({ account }) => {
  demoAccount = account;
  await startSession();
  $("#login-dialog").close();
});
$("#new-ticket").disabled = true;
startSession().then(refresh).catch((error) => {
  if (error.status === 401) $("#login-dialog").showModal();
  else $("#message").textContent = error.message;
});
setInterval(() => {
  if (session && document.visibilityState === "visible" && !document.querySelector("dialog[open]")) refresh();
}, 60000);
