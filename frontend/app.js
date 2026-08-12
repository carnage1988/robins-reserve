"use strict";
const CONFIG = window.ROBINS_CONFIG || {};
const API_BASE = String(CONFIG.API_BASE_URL || "").replace(/\/+$/, "");
const API = `${API_BASE}/api`;
const AUTH = `${API_BASE}/auth`;
const ENVIRONMENT = String(CONFIG.ENVIRONMENT || "Development");
const state = { authChecked: false, authenticated: false, user: null, page: "home", loading: false, error: "", data: {}, selected: null, mobileNav: false, sidebarCollapsed: false, lookup: null, lookupTimer: null };
const navGroups = [
  ["Operations", [["home", "⌂", "Dashboard"], ["orders", "⌕", "Orders"], ["products", "▦", "Products"]]],
  ["Community", [["league", "♟", "Pokémon League"]]],
  ["Events", [["robincon", "🎟", "RobinCon"], ["rc-orders", "≡", "RobinCon Orders"], ["rc-tickets", "◎", "RobinCon Tickets"], ["checkin", "✓", "Check-In"]]]
];
function esc(v) {
  return String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function yes(v) {
  return [true, 1, "1", "true", "yes", "y"].includes(typeof v === "string" ? v.trim().toLowerCase() : v);
}
function money(v) {
  if (v === null || v === undefined || v === "") return "Price unavailable";
  const n = Number(String(v).replace("£", "").replaceAll(",", ""));
  return Number.isFinite(n) ? `£${n.toFixed(2)}` : "Price unavailable";
}
function badge(v) {
  return yes(v) ? '<span class="status-badge status-approved">✓ Yes</span>' : '<span class="status-badge status-pending">No</span>';
}
function statusBadge(v) {
  const s = String(v || "Unknown").toLowerCase();
  return `<span class="status-pill status-${esc(["approved", "collected", "pending", "cancelled", "rejected"].includes(s) ? s : "default")}">${esc(v || "Unknown")}</span>`;
}
function pct(a, b) {
  return b ? Math.round((Number(a) || 0) * 100 / (Number(b) || 0)) : 0;
}
function formatDateTime(value) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
async function request(path, options = {}) {
  const res = await fetch(path, { credentials: "include", headers: { "Content-Type": "application/json", ...options.headers || {} }, ...options });
  const text = await res.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401 && path.startsWith(API)) {
      state.authenticated = false;
      render();
    }
    const d = body?.detail;
    throw new Error(typeof d === "string" ? d : d?.message || `Request failed (${res.status})`);
  }
  return body;
}
function api(path, options = {}) {
  return request(`${API}${path}`, options);
}
function toast(msg, type = "success") {
  const r = document.getElementById("toast-root");
  r.innerHTML = `<div class="toast ${type}">${esc(msg)}</div>`;
  setTimeout(() => r.innerHTML = "", 4200);
}
function login() {
  return `<main class="login-page"><section class="login-card"><div class="brand-mark login-brand">R</div><h1>Robins Reserve</h1><p>Operations Portal</p><a class="discord-login" href="${AUTH}/discord/login"><span>◉</span> Continue with Discord</a><div class="login-note">Authorised Robins staff only.</div></section></main>`;
}
function userBadge() {
  const u = state.user || {}, initial = esc((u.display_name || u.username || "S")[0]), avatar = u.avatar_url ? `<img class="staff-avatar" src="${esc(u.avatar_url)}" alt="" referrerpolicy="no-referrer" data-avatar-fallback="${initial}">` : `<span class="staff-avatar staff-avatar-fallback">${initial}</span>`;
  return `<div class="staff-user">${avatar}<div><strong>${esc(u.display_name || u.username || "Staff")}</strong><span>Discord staff</span></div></div><button class="btn btn-ghost" data-action="logout">Log out</button>`;
}
function shell(content) {
  const groups = navGroups.map(([title, items]) => `<div class="nav-section"><div class="nav-section-label">${esc(title)}</div><nav class="nav-list">${items.map(([id, icon, label]) => `<button class="nav-item ${state.page === id ? "active" : ""}" data-page="${id}" title="${esc(label)}"><span class="nav-icon">${icon}</span><span class="nav-label">${esc(label)}</span></button>`).join("")}</nav></div>`).join("");
  return `<div class="portal ${state.sidebarCollapsed ? "sidebar-collapsed" : ""}"><header class="topbar"><button class="btn btn-ghost mobile-menu" data-action="toggle-nav">☰</button><div class="brand"><div class="brand-mark">R</div><div class="brand-copy"><strong>Robins Reserve</strong><span>Operations Portal</span></div></div><div class="topbar-spacer"></div><div class="environment-pill">${esc(ENVIRONMENT)}</div>${userBadge()}</header><aside class="sidebar ${state.mobileNav ? "open" : ""}">${groups}<button class="sidebar-collapse" data-action="collapse-sidebar">${state.sidebarCollapsed ? "›" : "‹"}</button></aside><main class="main workspace"><div class="content">${content}</div></main></div>`;
}
function header(title, subtitle, actions = "") {
  return `<div class="page-header"><div><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div><div class="page-actions">${actions}</div></div>`;
}
function empty(msg, icon = "○") {
  return `<div class="empty-state"><div class="empty-icon">${icon}</div><strong>${esc(msg)}</strong></div>`;
}
function error() {
  return state.error ? `<div class="error-state">${esc(state.error)}</div>` : "";
}
function loading() {
  return state.loading ? `<div class="loading-state">Loading live data…</div>` : "";
}
function healthRow(name, item) {
  const on = !!item?.connected;
  return `<div class="health-row"><span class="health-light ${on ? "online" : "offline"}"></span><div><strong>${esc(name)}</strong><small>${esc(item?.message || (on ? "Operational" : "Unavailable"))}</small></div><span class="health-label ${on ? "online" : "offline"}">${on ? "Online" : "Offline"}</span></div>`;
}
function orderLines(order) {
  return `<div class="priced-lines">${(order.items || []).map((i) => `<div class="priced-line"><div><strong>${esc(i.product_name)}</strong><small>${esc(i.quantity)} × ${money(i.unit_price)}</small></div><b>${money(i.subtotal)}</b></div>`).join("")}</div><div class="basket-total"><span>Basket Total</span><strong>${money(order.total_value)}</strong></div>`;
}
function pendingCard(o) {
  return `<article class="approval-card"><div class="approval-head"><div><span class="record-eyebrow">Pickup PIN</span><h3>${esc(o.pickup_pin)}</h3></div>${statusBadge(o.status)}</div><p class="approval-customer">${esc(o.discord_username || o.customer || "Unknown customer")}</p>${orderLines(o)}<div class="record-actions"><button class="btn btn-success" data-action="approve" data-pin="${esc(o.pickup_pin)}">👍 Approve</button><button class="btn btn-danger" data-action="decline" data-pin="${esc(o.pickup_pin)}">👎 Decline</button></div></article>`;
}
function activityRows(rows) {
  if (!rows?.length) return empty("No recent activity.", "↻");
  return `<div class="activity-list">${rows.map((r) => `<div class="activity"><span class="activity-dot ${String(r.status).toLowerCase() === "approved" ? "online" : ""}"></span><div class="activity-main"><div class="activity-title">${esc(r.discord_username || "Customer")} · ${esc(r.pickup_pin)}</div><div class="activity-meta">${esc(r.status)} · ${esc(r.total_items)} item(s) · ${money(r.total_value)}</div></div></div>`).join("")}</div>`;
}
async function loadPage() {
  state.loading = true;
  state.error = "";
  render();
  try {
    if (state.page === "home") {
      const [dashResult, healthResult] = await Promise.allSettled([api("/dashboard"), api("/service-health")]);
      state.data = { dash: dashResult.status === "fulfilled" ? dashResult.value : {}, health: healthResult.status === "fulfilled" ? healthResult.value : { api: { connected: true, message: "Connected" } } };
      if (dashResult.status === "rejected") state.error = dashResult.reason.message;
    } else if (state.page === "orders") state.data = {};
    else if (state.page === "products") state.data = { items: await api("/products") };
else if (state.page === "league") {
  const [status, attendance, payments] = await Promise.all([
    api("/league/status"),
    api("/league/attendance"),
    api("/league/payments"),
  ]);

  state.data = {
    status,
    attendance,
    payments,
  };
}
    else if (state.page === "robincon") state.data = { summary: await api("/robincon/summary"), capacity: await api("/robincon/capacity"), tshirts: await api("/robincon/tshirts") };
    else if (state.page === "rc-orders") state.data = { items: await api("/robincon/orders") };
    else if (state.page === "rc-tickets") state.data = { items: await api("/robincon/tickets") };
    else if (state.page === "checkin") state.data = {};
  } catch (e) {
    state.error = e.message;
  }
  state.loading = false;
  render();
}
function homePage() {
  const d = state.data.dash || {}, h = state.data.health || {}, l = d.league || {}, pending = d.pending_orders || [];
  const all = Object.values(h).every((x) => x?.connected !== false);
  const stats = `<div class="compact-stats">
    <article class="compact-stat"><span>Orders Today</span><strong>${esc(d.orders_today ?? 0)}</strong><small>Preorders created</small></article>
    <article class="compact-stat"><span>Collections Today</span><strong>${esc(d.collections_today ?? 0)}</strong><small>Orders collected</small></article>
    <article class="compact-stat"><span>Pending Approvals</span><strong>${esc(d.reservations_waiting ?? 0)}</strong><small>Awaiting staff review</small></article>
    <article class="compact-stat"><span>League Attendance</span><strong>${esc(d.league_attendance ?? 0)}</strong><small>Checked in</small></article>
  </div>`;
  const quick = `<section class="panel quick-actions-panel"><div class="panel-header"><div><h2 class="panel-title">Quick Actions</h2><div class="panel-subtitle">Common staff operations</div></div></div><div class="quick-actions-grid"><button class="quick-action" data-action="start-league">▷ Start League</button><button class="quick-action" data-action="end-league">□ End League</button><button class="quick-action" data-page="orders">⌕ Orders / Lookup</button><button class="quick-action" data-page="robincon">🎟 RobinCon Dashboard</button></div></section>`;
  return header("Dashboard", "Live shop operations and service status", `<button class="btn btn-primary" data-action="refresh">Refresh</button>`) + error() + loading() + `
  <div class="ops-banner ${all ? "healthy" : "degraded"}"><span>${all ? "✓" : "!"}</span><strong>${all ? "All Systems Operational" : "Service Attention Required"}</strong><small>Last refreshed ${esc(d.updated_at || "now")}</small></div>
  <div class="dashboard-grid"><section class="panel health-panel"><div class="panel-header"><div><h2 class="panel-title">System Health</h2><div class="panel-subtitle">Live service checks</div></div></div>${healthRow("Discord Bot", h.discord_bot)}${healthRow("Google Sheets", h.google_sheets)}${healthRow("Dashboard API", h.api)}${healthRow("Pokémon League", h.pokemon_league)}${healthRow("RobinCon", h.robincon)}</section><section class="panel league-home"><div class="panel-header"><div><h2 class="panel-title">Pokémon League</h2><div class="panel-subtitle">Current in-store event</div></div>${l.active_event ? '<span class="status-pill status-approved">Running</span>' : '<span class="status-pill status-default">Not Running</span>'}</div><div class="league-hero"><strong>${l.active_event ? esc(l.active_event["Store Code"] || l.store_code || "—") : "Not Running"}</strong><span>${l.active_event ? "Store Code" : "No active League event"}</span></div><div class="league-mini"><div><small>Attendance</small><b>${esc(l.attendance_count ?? 0)}</b></div><div><small>Ends</small><b>${esc(l.active_event?.["End Time"] || "—")}</b></div></div><button class="btn btn-ghost" data-page="league">Open League</button></section></div>
  ${stats}
  <section class="panel approvals-panel"><div class="panel-header"><div><h2 class="panel-title">Pending Approvals</h2><div class="panel-subtitle">Approve or decline through the normal Discord workflow</div></div><span class="chip">${pending.length} waiting</span></div>${pending.length ? `<div class="approval-grid compact-approval-grid">${pending.map(pendingCard).join("")}</div>` : empty("No orders are waiting for approval.", "✓")}</section>
  <div class="lower-dashboard-grid"><section class="panel recent-panel"><div class="panel-header"><div><h2 class="panel-title">Recent Activity</h2><div class="panel-subtitle">Latest preorder lifecycle changes</div></div></div>${activityRows(d.recent_activity)}</section>${quick}</div>`;
}
function ordersPage() {
  const o = state.lookup;
  return header("Orders", "Search and collect reservations by pickup PIN") + error() + `<section class="panel pin-lookup"><form data-form="pin-lookup"><label>Pickup PIN</label><div class="pin-input-row"><input name="pin" autocomplete="off" autofocus placeholder="Enter pickup PIN"><button class="btn btn-primary">Search</button></div></form><small>Lookup results reset automatically after 60 seconds of inactivity.</small></section>${o ? orderCard(o) : empty("Waiting for a PIN", "⌕")}`;
}
function orderCard(o) {
  const status = String(o.status || "").toLowerCase();
  return `<article class="panel record-card order-result"><div class="record-head"><div><span class="record-eyebrow">Pickup PIN</span><h2 class="record-title">${esc(o.pickup_pin)}</h2></div>${statusBadge(o.status)}</div><div class="detail-grid"><div class="detail"><div class="detail-label">Customer</div><div class="detail-value">${esc(o.discord_username || "Unknown")}</div></div><div class="detail"><div class="detail-label">Total Items</div><div class="detail-value">${esc(o.total_quantity || 0)}</div></div><div class="detail"><div class="detail-label">Approved By</div><div class="detail-value">${esc(o.approved_by || "—")}</div></div><div class="detail"><div class="detail-label">Source</div><div class="detail-value">${esc(o.sheet_name || "Preorders")}</div></div></div>${orderLines(o)}<div class="record-actions">${status === "pending" ? `<button class="btn btn-success" data-action="approve" data-pin="${esc(o.pickup_pin)}">👍 Approve</button><button class="btn btn-danger" data-action="decline" data-pin="${esc(o.pickup_pin)}">👎 Decline</button>` : ""}${status === "approved" ? `<button class="btn btn-primary" data-action="collect" data-pin="${esc(o.pickup_pin)}">✓ Collect Order</button>` : ""}<button class="btn btn-ghost" data-action="clear-lookup">Clear</button></div></article>`;
}
function productsPage() {
  const rows = state.data.items || [];
  return header("Products", "Preorder catalogue, pricing and stock") + error() + loading() + (rows.length ? `<div class="card-grid">${rows.map((r) => `<article class="data-card"><h3>${esc(r.product_name || r["Product Name"])}</h3><p><code>${esc(r.order_code || r["Order Code"])}</code></p><div class="product-facts"><strong>${esc(r.stock ?? r.Stock ?? 0)} in stock</strong><b>${money(r.unit_price)}</b></div></article>`).join("")}</div>` : empty("No products found."));
}
function leaguePage() {
    const paymentData = state.data.payments || {};
    const attendees = paymentData.attendees || [];
    const dbSession = paymentData.session || null;

    const s = state.data.status || {};
    const e = s.active_event;

    const paymentRows = attendees.map((attendee) => {
        const payment = attendee.payment;

        let paymentText = "No payment";
        let paymentClass = "status-default";
        let actions = "—";

        if (payment) {
            if (payment.status === "cash_due") {
                paymentText = `💷 Cash Due · ${money(payment.amount)}`;
                paymentClass = "status-pending";

                actions = `
                    <div class="record-actions">
                        <button
                            class="btn btn-success"
                            data-action="league-cash-paid"
                            data-payment-id="${esc(payment.id)}"
                        >
                            💷 Mark Cash Paid
                        </button>

                        <button
                            class="btn btn-ghost"
                            data-action="league-comp"
                            data-payment-id="${esc(payment.id)}"
                            data-customer="${esc(attendee.customer || "Player")}"
                        >
                            🎟 Comp Entry
                        </button>
                    </div>
                `;
            } else if (payment.status === "paid") {
                paymentText = `✓ Paid · ${money(payment.amount)}`;
                paymentClass = "status-approved";
            } else if (payment.status === "comped") {
                paymentText = `🎟 Comped · ${money(payment.amount)}`;
                paymentClass = "status-approved";
            } else {
                paymentText = `${payment.status} · ${money(payment.amount)}`;
            }
        }

        return `
            <tr>
                <td>
                    <strong>
                        ${esc(attendee.customer || attendee.discord_user_id || "Unknown")}
                    </strong>
                </td>

                <td>
                    ${esc(formatDateTime(attendee.checked_in_at))}
                </td>

                <td>
                    <span class="status-pill ${paymentClass}">
                        ${paymentText}
                    </span>
                </td>

                <td>
                    ${actions}
                </td>
            </tr>
        `;
    }).join("");

    return (
        header(
            "Pokémon League",
            "Current event, attendance and payments",
            `
                <button
                    class="btn ${e ? "btn-danger" : "btn-success"}"
                    data-action="${e ? "end-league" : "start-league"}"
                >
                    ${e ? "End Event" : "Start Event"}
                </button>
            `
        )
        + error()
        + loading()
        + `
            <div class="league-status-card ${e ? "running" : "inactive"}">
                <span>
                    ${e ? "Running" : "Not Running"}
                </span>

                <strong>
                    ${e ? esc(e["Store Code"] || "—") : "No active event"}
                </strong>

                <small>
                    ${
                        e
                            ? `${esc(attendees.length)} player(s) checked in`
                            : "Start an event to generate a store code"
                    }
                </small>
            </div>

            ${
                e
                    ? `
                        <div class="stats-grid">
                            <article class="stat-card">
                                <span class="stat-label">Attendance</span>
                                <div class="stat-value">
                                    ${esc(attendees.length)}
                                </div>
                            </article>

                            <article class="stat-card">
                                <span class="stat-label">Store Code</span>
                                <div class="stat-value mono">
                                    ${esc(e["Store Code"] || "—")}
                                </div>
                            </article>

                            <article class="stat-card">
                                <span class="stat-label">Entry Fee</span>
                                <div class="stat-value">
                                    ${dbSession ? money(dbSession.entry_fee) : "—"}
                                </div>
                            </article>

                            <article class="stat-card">
                                <span class="stat-label">Ends</span>
                                <div class="stat-value small-value">
                                    ${esc(formatDateTime(dbSession?.ends_at || e["End Time"]))}
                                </div>
                            </article>
                        </div>
                    `
                    : ""
            }

            ${
                attendees.length
                    ? `
                        <div class="table-card league-payments-table">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Player</th>
                                        <th>Checked In</th>
                                        <th>Payment</th>
                                        <th>Actions</th>
                                    </tr>
                                </thead>

                                <tbody>
                                    ${paymentRows}
                                </tbody>
                            </table>
                        </div>
                    `
                    : empty("No attendance records.")
            }
        `
    );
}
function capacityBlocks(cap) {
  return Object.entries(cap || {}).map(([day, events]) => `<section class="panel"><h2>${esc(day)}</h2>${events.map((e) => {
    const r = e.Registered || 0, c = e.Capacity || 0;
    return `<div class="capacity-row"><div><strong>${esc(e["Event Name"])}</strong><span>${r} / ${c}</span></div><div class="progress"><i style="width:${Math.min(100, pct(r, c))}%"></i></div></div>`;
  }).join("") || empty("No active events.")}</section>`).join("");
}
function robinconPage() {
  const s = state.data.summary || {}, cap = state.data.capacity || {}, shirts = state.data.tshirts || {};
  return header("RobinCon", "Live event operations", `<button class="btn btn-primary" data-page="checkin">Open Check-In</button>`) + error() + loading() + `<div class="metric-grid">${[["Orders", s.orders ?? 0], ["Tickets", s.tickets ?? 0], ["Linked", s.linked ?? 0], ["Registered", s.registered ?? 0], ["Checked In", s.checked_in ?? 0]].map(([a, b]) => `<article class="metric-card"><span>${a}</span><strong>${b}</strong></article>`).join("")}</div><div class="split-grid"><div>${capacityBlocks(cap)}</div><section class="panel"><h2>T-Shirts</h2>${Object.entries(shirts).map(([k, v]) => `<div class="summary-line"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("") || empty("No sizes selected.")}</section></div>`;
}
function rcOrdersPage() {
  const rows = state.data.items || [];
  return header("RobinCon Orders", "Purchases and family ticket groups", `<form class="search-form" data-form="order-search"><input name="q" placeholder="Search orders…"><button class="btn btn-primary">Search</button></form>`) + error() + loading() + (rows.length ? `<div class="table-card"><table><thead><tr><th>Order</th><th>Customer</th><th>Email</th><th>Quantity</th><th>Status</th></tr></thead><tbody>${rows.map((r) => `<tr class="clickable" data-action="open-order" data-id="${esc(r["Order Number"])}"><td><code>${esc(r["Order Number"])}</code></td><td>${esc(r["Customer Name"])}</td><td>${esc(r["Customer Email"])}</td><td>${esc(r.Quantity)}</td><td>${esc(r["Payment Status"] || r["Order Status"])}</td></tr>`).join("")}</tbody></table></div>` : empty("No RobinCon orders found."));
}
function ticketRow(r) {
  return `<tr class="clickable" data-action="open-ticket" data-id="${esc(r["Ticket ID"])}"><td><code>${esc(r["Ticket ID"])}</code></td><td>${esc(r["Ticket Holder Name"] || "Unclaimed")}</td><td>${esc(r["Order Number"])}</td><td>${badge(r["Registration Complete"])}</td><td>${badge(r["Checked In"])}</td></tr>`;
}
function ticketsPage() {
  const rows = state.data.items || [];
  return header("RobinCon Tickets", "Search, inspect and manage attendees", `<form class="search-form" data-form="ticket-search"><input name="q" placeholder="Ticket, order, attendee, Discord…"><button class="btn btn-primary">Search</button></form>`) + error() + loading() + (rows.length ? `<div class="table-card"><table><thead><tr><th>Ticket</th><th>Attendee</th><th>Order</th><th>Registered</th><th>Checked In</th></tr></thead><tbody>${rows.map(ticketRow).join("")}</tbody></table></div>` : empty("No tickets found."));
}
function checkinPage() {
  return header("Event Check-In", "Enter or scan a RobinCon ticket ID") + error() + `<section class="checkin-panel"><form data-form="checkin"><label>Ticket ID</label><input name="ticket_id" autofocus autocomplete="off" placeholder="RC27-000001"><button class="btn btn-success btn-large">Check In</button></form><div id="checkin-result"></div></section>`;
}
function detailModal() {
  const x = state.selected;
  if (!x) return "";
  if (x.tickets) {
    return `<div class="modal-backdrop" data-action="close-modal"><section class="modal" onclick="event.stopPropagation()"><button class="modal-close" data-action="close-modal">×</button><h2>Order ${esc(x.order?.["Order Number"])}</h2><p>${esc(x.order?.["Customer Name"] || "")} · ${esc(x.order?.["Customer Email"] || "")}</p><div class="ticket-list">${x.tickets.map((t) => `<button class="ticket-list-item" data-action="open-ticket" data-id="${esc(t["Ticket ID"])}"><strong>${esc(t["Ticket ID"])}</strong><span>${esc(t["Ticket Holder Name"] || "Unclaimed")}</span>${badge(t["Registration Complete"])}</button>`).join("")}</div></section></div>`;
  }
  return `<div class="modal-backdrop" data-action="close-modal"><section class="modal" onclick="event.stopPropagation()"><button class="modal-close" data-action="close-modal">×</button><h2>${esc(x["Ticket ID"])}</h2><div class="detail-grid">${[["Attendee", "Ticket Holder Name"], ["Order", "Order Number"], ["Discord", "Discord Username"], ["T-Shirt", "T-Shirt Size"], ["Saturday", "Saturday Event Name"], ["Sunday", "Sunday Event Name"]].map(([n, k]) => `<div><span>${n}</span><strong>${esc(x[k] || "Not set")}</strong></div>`).join("")}</div><div class="modal-actions">${yes(x["Checked In"]) ? `<button class="btn btn-danger" data-action="uncheckin" data-id="${esc(x["Ticket ID"])}">Undo Check-In</button>` : `<button class="btn btn-success" data-action="checkin-id" data-id="${esc(x["Ticket ID"])}">Check In</button>`}</div><form class="edit-form" data-form="edit-ticket" data-id="${esc(x["Ticket ID"])}"><select name="field"><option value="attendee">Attendee name</option><option value="tshirt">T-shirt</option><option value="saturday">Saturday event</option><option value="sunday">Sunday event</option></select><input name="value" placeholder="New value"><button class="btn btn-primary">Save change</button></form></section></div>`;
}
function page() {
  if (state.page === "home") return homePage();
  if (state.page === "orders") return ordersPage();
  if (state.page === "products") return productsPage();
  if (state.page === "league") return leaguePage();
  if (state.page === "robincon") return robinconPage();
  if (state.page === "rc-orders") return rcOrdersPage();
  if (state.page === "rc-tickets") return ticketsPage();
  if (state.page === "checkin") return checkinPage();
  return empty("Page not found.");
}
function render() {
  const root = document.getElementById("app");
  if (!state.authChecked) {
    root.innerHTML = '<div class="loading-screen">Loading…</div>';
    return;
  }
  root.innerHTML = state.authenticated ? shell(page()) + (state.mobileNav ? '<button class="nav-backdrop" data-action="toggle-nav"></button>' : "") + detailModal() : login();
  bind();
}
function resetLookupTimer() {
  clearTimeout(state.lookupTimer);
  state.lookupTimer = setTimeout(() => {
    state.lookup = null;
    if (state.page === "orders") render();
  }, 60000);
}
async function reservationAction(pin, action, reason = "") {
  try {
    const result = await api(`/reservations/${encodeURIComponent(pin)}/${action}`, { method: "POST", body: JSON.stringify({ reason }) });
    const warnings = result.notification_warnings || [];
    toast(action === "approve" ? "Order approved and Discord workflow completed." : action === "decline" ? "Order declined and Discord workflow completed." : "Order collected.", warnings.length ? "error" : "success");
    if (warnings.length) toast(`Order updated, but notification warning: ${warnings[0]}`, "error");
    state.lookup = action === "collect" ? result : null;
    await loadPage();
  } catch (e) {
    toast(e.message, "error");
  }
}
async function actionCheckin(id, undo = false) {
  try {
    const t = await api(`/robincon/${undo ? "uncheckin" : "checkin"}`, { method: "POST", body: JSON.stringify({ ticket_id: id }) });
    toast(undo ? "Check-in reversed." : "Attendee checked in.");
    state.selected = t;
    await loadPage();
  } catch (e) {
    toast(e.message, "error");
  }
}
async function markLeagueCashPaid(paymentId) {
  if (!confirm("Confirm that the cash payment has been received?")) {
    return;
  }

  try {
    await api(
      `/payments/${encodeURIComponent(paymentId)}/confirm-cash`,
      {
        method: "POST",
      }
    );

    toast("Cash payment marked as paid.");
    await loadPage();
  } catch (error) {
    toast(error.message, "error");
  }
}


async function compLeagueEntry(paymentId, customer) {
  const displayName = customer || "this player";

  const reason = prompt(
    `Reason for comping ${displayName}'s League entry:`,
    "Goodwill"
  );

  if (reason === null) {
    return;
  }

  if (!reason.trim()) {
    toast("A comp reason is required.", "error");
    return;
  }

  try {
    await api(
      `/payments/${encodeURIComponent(paymentId)}/comp`,
      {
        method: "POST",
        body: JSON.stringify({
          reason: reason.trim(),
        }),
      }
    );

    toast(`${displayName}'s League entry has been comped.`);
    await loadPage();
  } catch (error) {
    toast(error.message, "error");
  }
}

function bind() {
  document.querySelectorAll("img[data-avatar-fallback]").forEach((img) => img.onerror = () => {
    const span = document.createElement("span");
    span.className = "staff-avatar staff-avatar-fallback";
    span.textContent = img.dataset.avatarFallback || "S";
    img.replaceWith(span);
  });
  document.querySelectorAll("[data-page]").forEach((el) => el.onclick = () => {
    state.page = el.dataset.page;
    state.selected = null;
    state.mobileNav = false;
    state.lookup = null;
    loadPage();
  });
  document.querySelectorAll("[data-action]").forEach((el) => el.onclick = async () => {
    const a = el.dataset.action;
    if (a === "logout") {
      await request(`${AUTH}/logout`, { method: "POST" });
      location.reload();
    } else if (a === "toggle-nav") {
      state.mobileNav = !state.mobileNav;
      render();
    } else if (a === "collapse-sidebar") {
      state.sidebarCollapsed = !state.sidebarCollapsed;
      render();
    } else if (a === "refresh") loadPage();
    else if (a === "clear-lookup") {
      state.lookup = null;
      clearTimeout(state.lookupTimer);
      render();
    } else if (a === "approve") reservationAction(el.dataset.pin, "approve");
    else if (a === "decline") {
      const reason = prompt("Reason for declining this order:", "Staff declined reservation");
      if (reason !== null) reservationAction(el.dataset.pin, "decline", reason);
    } else if (a === "collect") reservationAction(el.dataset.pin, "collect");
    else if (a === "start-league") {
      try {
        const result = await api("/league/start", { method: "POST" });
        const warnings = result.notification_warnings || [];
        toast(warnings.length ? "League started, but Discord announcement failed." : "League event started and posted to Discord.", warnings.length ? "error" : "success");
        loadPage();
      } catch (e) {
        toast(e.message, "error");
      }
      } else if (a === "end-league") {
        if (confirm("End the current League event?")) {
          try {
            const result = await api("/league/end", {
              method: "POST",
            });

            const warnings = result.notification_warnings || [];

            toast(
              warnings.length
                ? "League ended, but Discord announcement failed."
                : "League event ended and posted to Discord.",
              warnings.length ? "error" : "success"
            );

            loadPage();
          } catch (e) {
            toast(e.message, "error");
          }
        }

      } else if (a === "league-cash-paid") {
        await markLeagueCashPaid(
          el.dataset.paymentId
        );

      } else if (a === "league-comp") {
        await compLeagueEntry(
          el.dataset.paymentId,
          el.dataset.customer
        );

      } else if (a === "close-modal") {
        state.selected = null;
        render();

      } else if (a === "open-ticket") {
        state.selected = await api(
          `/robincon/tickets/${encodeURIComponent(el.dataset.id)}`
        );
        render();

      } else if (a === "open-order") {
        state.selected = await api(
          `/robincon/orders/${encodeURIComponent(el.dataset.id)}`
        );
        render();

      } else if (a === "checkin-id") {
        actionCheckin(el.dataset.id);

      } else if (a === "uncheckin") {
        actionCheckin(el.dataset.id, true);
      }
  });
  document.querySelectorAll("form[data-form]").forEach((f) => f.onsubmit = async (ev) => {
    ev.preventDefault();
    const fd = new FormData(f), kind = f.dataset.form;
    try {
      if (kind === "pin-lookup") {
        const pin = String(fd.get("pin") || "").trim();
        if (!pin) throw new Error("Enter a pickup PIN.");
        state.lookup = await api(`/reservations/${encodeURIComponent(pin)}`);
        resetLookupTimer();
        render();
      } else if (kind === "ticket-search") {
        state.data.items = await api(`/robincon/tickets?search=${encodeURIComponent(fd.get("q"))}`);
        render();
      } else if (kind === "order-search") {
        state.data.items = await api(`/robincon/orders?search=${encodeURIComponent(fd.get("q"))}`);
        render();
      } else if (kind === "checkin") {
        const id = String(fd.get("ticket_id") || "").trim();
        const t = await api("/robincon/checkin", { method: "POST", body: JSON.stringify({ ticket_id: id }) });
        document.getElementById("checkin-result").innerHTML = `<div class="checkin-success"><strong>✓ Checked In</strong><span>${esc(t["Ticket Holder Name"] || t["Ticket ID"])}</span><small>${esc(t["Ticket ID"])}</small></div>`;
        f.reset();
      } else if (kind === "edit-ticket") {
        const id = f.dataset.id;
        state.selected = await api(`/robincon/tickets/${encodeURIComponent(id)}/edit`, { method: "POST", body: JSON.stringify({ field: fd.get("field"), value: fd.get("value") }) });
        toast("Ticket updated.");
        render();
      }
    } catch (e) {
      toast(e.message, "error");
    }
  });
}
(async function init() {
  try {
    const me = await request(`${AUTH}/me`);
    state.authenticated = !!me.authenticated;
    state.user = me.user || null;
  } catch {
    state.authenticated = false;
  }
  state.authChecked = true;
  render();
  if (state.authenticated) loadPage();
})();