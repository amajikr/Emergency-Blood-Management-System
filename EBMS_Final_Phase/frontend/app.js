const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATE_KIND = { READY: "blue", RUNNING: "purple", WAITING: "orange", COMPLETED: "green", FAILED: "red", NEW: "grey" };
const fmtIST = (ts) => {
  if (!ts) return "";
  const d = new Date(ts.replace(" ", "T") + "Z");
  return isNaN(d) ? ts : d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "medium" });
};
const dash = (v) => (v === null || v === undefined || v === "" ? "-" : v);
const badge = (text, kind) => `<span class="badge ${kind}">${esc(text)}</span>`;

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

function toast(msg, bad = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = bad ? "show bad" : "show";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.className = ""), 3500);
}

// cells must already be escaped / HTML-safe
function table(headers, rows) {
  if (!rows.length) return '<p class="hint">Nothing here yet.</p>';
  const head = headers.map((h) => `<th>${esc(h)}</th>`).join("");
  const body = rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function fill(select, pairs) {
  select.innerHTML = pairs.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
}

async function loadMeta() {
  const m = await api("/api/meta");
  const r = $("request-form"), d = $("donation-form"), t = $("transfer-form"), match = $("matching-form");
  fill(r.hospital_id, m.hospitals.map((h) => [h.id, h.name]));
  fill(r.component_id, m.components.map((c) => [c.id, c.name]));
  fill(r.required_group_id, m.groups.map((g) => [g, g]));
  fill(r.urgency, m.urgencies.map((u) => [u, u === "URGENT" ? "HIGH" : u === "SCHEDULE" ? "NORMAL" : "CRITICAL"]));
  fill(d.blood_group_id, m.groups.map((g) => [g, g]));
  fill(d.component_id, m.components.map((c) => [c.id, c.name]));
  const dh = d.hospital_id; dh.innerHTML = '<option value="">Unassigned central stock</option>' + m.hospitals.map((h) => `<option value="${esc(h.id)}">${esc(h.name)}</option>`).join("");
  fill(t.source_hospital_id, m.hospitals.map((h) => [h.id, h.name]));
  fill(t.destination_hospital_id, m.hospitals.map((h) => [h.id, h.name]));
  fill(t.blood_group_id, m.groups.map((g) => [g, g])); fill(t.component_id, m.components.map((c) => [c.id, c.name]));
  fill(match.hospital_id, m.hospitals.map((h) => [h.id, h.name]));
  fill(match.required_group_id, m.groups.map((g) => [g, g])); fill(match.component_id, m.components.map((c) => [c.id, c.name]));
  for (const id of ["expiry-group", "analytics-group", "report-group"]) $(id).innerHTML = '<option value="">All groups</option>' + m.groups.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("");
  $("hospital-list").innerHTML = table(["Hospital ID", "Hospital", "Network status"], m.hospitals.map((h) => [esc(h.id), esc(h.name), badge("ACTIVE", "green")]));
}

async function refresh() {
  try {
    const [inv, reqs, bk, auditData, plan, dash] = await Promise.all([
      api("/api/inventory"), api("/api/requests"), api("/api/banker"),
      api("/api/audit?limit=50"), api("/api/query-plan"), api("/api/dashboard"),
    ]);
    $("inventory").innerHTML = table(["Component", "Group", "Available", "Next expiry"],
      inv.map((r) => [esc(r.component_id), esc(r.blood_group_id), esc(r.available), esc(r.next_expiry)]));
    $("requests").innerHTML = table(["#", "Hospital", "Component", "Group", "Qty", "Urgency", "State", "Tracking"],
      reqs.map((r) => [esc(r.request_id), esc(r.hospital), esc(r.component_id), esc(r.required_group_id), esc(r.quantity),
        badge(r.urgency === "URGENT" ? "HIGH" : r.urgency === "SCHEDULE" ? "NORMAL" : r.urgency, r.urgency === "CRITICAL" ? "red" : r.urgency === "URGENT" ? "orange" : "blue"),
        badge(r.process_state, STATE_KIND[r.process_state] || "grey"), `<a class="text-link" href="#tracking-sec" data-track="${esc(r.request_id)}">View</a>`]));
    let verdict = bk.safe === null ? badge("CHECK FAILED", "red") : bk.safe ? badge("SAFE", "green") : badge("UNSAFE", "red");
    const seq = bk.error ? esc(bk.error) : bk.sequence.length ? `Safe sequence: ${esc(bk.sequence.join(" → "))}` : "No active processes.";
    $("banker").innerHTML = `<p>${verdict} ${seq}</p>` + table(["Component", "Available"], Object.entries(bk.available).map(([k, v]) => [esc(k), esc(v)]));
    const audits = auditData.rows || [];
    $("audit").innerHTML = renderAudit(audits);
    const u = dash.units || {}, r = dash.requests || {};
    $("stat-total").textContent = u.total ?? "—"; $("stat-available").textContent = u.available ?? "—";
    $("stat-reserved").textContent = u.reserved ?? "—"; $("stat-ready").textContent = r.pending ?? "—";
    $("stat-completed").textContent = r.completed ?? "—"; $("stat-critical").textContent = r.critical ?? "—";
    $("stat-expiring").textContent = u.expiring ?? "—"; $("stat-expired").textContent = u.expired ?? "—";
    $("stat-hospitals").textContent = dash.hospitals ?? "—";
    if ($("stat-waiting")) $("stat-waiting").textContent = (r.by_status || {}).WAITING || 0;
    renderGroupChart(dash.groups || []); renderRequestSummary(r.by_status || {}); renderRecent(dash.recent_activity || []);
    if ($("plan")) $("plan").textContent = plan.map((p) => p.detail).join("\n") || "no plan";
    $("today-label").textContent = "Updated " + new Date().toLocaleTimeString("en-IN", {hour:"2-digit",minute:"2-digit"});
    await Promise.allSettled([loadExpiry(), loadTransfers(), loadAnalytics()]);
  } catch (e) { toast(e.message, true); }
}

function renderAudit(rows) {
  return table(["When", "Actor", "Event", "Entity", "Transaction", "Reason", "Previous → New"],
    rows.map((a) => [esc(fmtIST(a.created_at)), esc(a.actor), esc(a.event_type), esc(`${a.entity_type} ${a.entity_id}`),
      esc(dash(a.txn_id)), esc(dash(a.reason)), esc(`${a.before_state || ""} → ${a.after_state || ""}`)]));
}
function renderGroupChart(rows) {
  const max = Math.max(1, ...rows.map((r) => Number(r.total || 0)));
  $("group-chart").innerHTML = rows.map((r) => `<div class="bar-row"><span>${esc(r.blood_group_id)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2,Number(r.total||0)/max*100)}%"></div></div><strong>${esc(r.total||0)}</strong></div>`).join("") || '<p class="hint">No inventory records.</p>';
}
function renderRequestSummary(counts) {
  const rows=[["Ready",counts.READY||0,""],["Waiting",counts.WAITING||0,""],["In process",counts.RUNNING||0,""],["Completed",counts.COMPLETED||0,"complete"],["Failed",counts.FAILED||0,"critical"]];
  $("request-summary").innerHTML=rows.map(([name,n,kind])=>`<div class="status-line"><span><i class="status-mark ${kind}"></i>${esc(name)}</span><b>${esc(n)}</b></div>`).join("");
}
function renderRecent(rows) {
  $("recent-activity").innerHTML=rows.map((x)=>`<div class="activity-item"><strong>${esc(x.event_type)} · ${esc(x.entity_type)} ${esc(x.entity_id)}</strong><span>${esc(x.actor)} · ${esc(fmtIST(x.created_at))}</span></div>`).join("")||'<p class="hint">No activity recorded yet.</p>';
}

async function action(fn) {
  try {
    await fn();
    await refresh();
  } catch (e) {
    toast(e.message, true);
  }
}

$("refresh").onclick = () => action(async () => { await refreshAudit(); toast("Dashboard refreshed"); });

$("request-form").onsubmit = (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  action(async () => {
    const r = await api("/api/requests", { method: "POST", body: JSON.stringify(body) });
    toast(`Process ${r.request_id} created (READY)`);
  });
};

$("donation-form").onsubmit = (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  action(async () => {
    const r = await api("/api/donations", { method: "POST", body: JSON.stringify(body) });
    toast(`${r.unit_ids.length} units added, ${r.woken} waiting request(s) woken`);
  });
};

$("tick").onclick = () => action(async () => {
  const r = await api("/api/scheduler/tick", { method: "POST" });
  $("tick-result").textContent = JSON.stringify(r, null, 2);
  toast(r.state ? `Process ${r.request_id}: ${r.state}` : r.message);
});

$("expire").onclick = () => action(async () => {
  const r = await api("/api/expiry/run", { method: "POST" });
  toast(`Expiry monitor flagged ${r.expired} unit(s)`);
});

loadMeta().then(async () => { await refresh(); await refreshAudit(); }).catch((e) => toast(e.message, true));

const navLinks = [...document.querySelectorAll(".menu a")];
const sectionObserver = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (!entry.isIntersecting) return;
    navLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + entry.target.id));
  });
}, { rootMargin: "-40% 0px -55% 0px" });
document.querySelectorAll("section[id]").forEach((sec) => sectionObserver.observe(sec));
navLinks.forEach((a) => a.addEventListener("click", () => { const target=document.querySelector(a.getAttribute("href")); if(target){const heading=target.querySelector("h1,h2,h3"); if(heading) $("page-title").textContent=heading.textContent;} }));


// Data-backed modules: matching, expiry, analytics, tracking, transfers, exports, and access control.
let auditOffset=0; const auditLimit=50;
function queryString(values){const p=new URLSearchParams();Object.entries(values).forEach(([k,v])=>{if(v)p.set(k,v)});return p.toString()}
async function refreshAudit(){
  const filters={search:$("audit-search").value,event:$("audit-event").value,actor:$("audit-actor").value,txn_id:$("audit-txn").value,from:$("audit-from").value,to:$("audit-to").value,limit:auditLimit,offset:auditOffset};
  const q=queryString(filters),d=await api("/api/audit?"+q);$("audit").innerHTML=renderAudit(d.rows);$("audit-page").textContent=`${d.total?auditOffset+1:0}–${Math.min(auditOffset+auditLimit,d.total)} of ${d.total}`;
  const exportQ=queryString({...filters,format:"csv"});$("audit-csv").href="/api/audit?"+exportQ;$("audit-pdf").href="/api/audit?"+queryString({...filters,format:"pdf"});
}
async function loadExpiry(){
  const q=queryString({group:$("expiry-group").value,from:$("expiry-from").value,to:$("expiry-to").value});
  const rows=await api("/api/expiry?"+q);$("expiry-list").innerHTML=table(["Unit", "Component", "Group", "Hospital ID", "Expiry", "Status"],rows.map(x=>[esc(x.unit_id),esc(x.component_id),esc(x.blood_group_id),esc(x.hospital_id??"Central"),esc(x.expiry_date),badge(x.status==="EXPIRED"?"EXPIRED":"EXPIRING",x.status==="EXPIRED"?"red":"orange")]));
}
async function loadTransfers(){
  const rows=await api("/api/transfers");$("transfers-list").innerHTML=table(["#","Source → Destination","Stock","Qty","Status","Next step"],rows.map(t=>{
    const next={PENDING:"APPROVED",APPROVED:"IN_TRANSIT",IN_TRANSIT:"COMPLETED"}[t.status];
    const action=next?`<button class="btn btn-light transfer-next" data-id="${esc(t.transfer_id)}" data-status="${next}">${next.replace("_"," ")}</button>`:"—";
    return [esc(t.transfer_id),`${esc(t.source)} → ${esc(t.destination)}`,`${esc(t.blood_group_id)} ${esc(t.component_id)}`,esc(t.quantity),badge(t.status.replace("_"," "),t.status==="COMPLETED"?"green":"orange"),action];
  }));
}
function miniBars(title, rows, labelKey, valueKey){
  const max=Math.max(1,...rows.map(x=>Number(x[valueKey]||0)));
  return `<div class="analytics-panel"><h3>${esc(title)}</h3>${rows.length?rows.slice(0,12).map(x=>`<div class="bar-row"><span>${esc(x[labelKey]??"—")}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2,Number(x[valueKey]||0)/max*100)}%"></div></div><strong>${esc(x[valueKey]||0)}</strong></div>`).join(""):'<p class="hint">No matching records.</p>'}</div>`;
}
async function loadAnalytics(){
  const q=queryString({from:$("analytics-from").value,to:$("analytics-to").value,group:$("analytics-group").value});const d=await api("/api/analytics?"+q);
  const stock=d.inventory.map(x=>({label:`${x.blood_group_id} ${x.status.toLowerCase()}`,value:x.count}));
  const exp=d.expiring.map(x=>({label:x.expiry_date,value:x.count})); const donation=d.donations.map(x=>({label:x.month,value:x.units}));
  const hospitals=d.hospitals.map(x=>({label:x.hospital.split(" ").slice(0,2).join(" "),value:x.units}));
  const requests=d.requests.map(x=>({label:`${x.urgency} ${x.process_state}`,value:x.count}));
  $("analytics-view").innerHTML=miniBars("Stock by group and status",stock,"label","value")+miniBars("Expiry dates",exp,"label","value")+miniBars("Monthly donations",donation,"label","value")+miniBars("Hospital inventory",hospitals,"label","value")+miniBars("Requests by urgency and state",requests,"label","value");
}
$("matching-form").onsubmit=(e)=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));action(async()=>{const r=await api("/api/matching",{method:"POST",body:JSON.stringify(d)});$("matching-result").innerHTML=`<p>${r.sufficient?badge("ENOUGH MATCHES","green"):badge("SHORTFALL","orange")} ${esc(r.available)} compatible units available for ${esc(r.request.quantity)} requested for ${esc(r.request.hospital||"hospital request")}.</p><p class="hint">Compatible groups: ${esc(r.compatible_groups.join(", ")||"none")}. FEFO order applied. Simulation only.</p>`+table(["Unit","Group","Component","Expiry","Location"],r.units.map(u=>[esc(u.unit_id),esc(u.blood_group_id),esc(u.component_id),esc(u.expiry_date),esc(u.location)]));});};
$("transfer-form").onsubmit=(e)=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));action(async()=>{const r=await api("/api/transfers",{method:"POST",body:JSON.stringify(d)});toast(`Transfer ${r.transfer_id} reserved and queued`);e.target.reset();await loadMeta();await loadTransfers();});};
$("transfers-list").onclick=(e)=>{const b=e.target.closest(".transfer-next");if(!b)return;action(async()=>{await api(`/api/transfers/${b.dataset.id}`,{method:"PATCH",body:JSON.stringify({status:b.dataset.status})});await loadTransfers();toast(`Transfer moved to ${b.dataset.status.replace("_"," ")}`);});};
$("reload-transfers").onclick=()=>loadTransfers().catch(e=>toast(e.message,true));
$("expiry-filter").onclick=()=>loadExpiry().catch(e=>toast(e.message,true));$("expiry-refresh").onclick=()=>loadExpiry().catch(e=>toast(e.message,true));
$("analytics-filter").onclick=()=>loadAnalytics().catch(e=>toast(e.message,true));
$("tracking-form").onsubmit=(e)=>{e.preventDefault();const id=e.target.request_id.value;action(async()=>{const d=await api(`/api/requests/${id}/history`);const order=["PENDING","SCHEDULED","RESERVED","COMPLETED"];const done=new Set(d.history.map(x=>x.status));$("tracking-result").innerHTML=`<div class="timeline">${order.map(s=>`<div class="timeline-step ${done.has(s)?"done":""}"><i></i>${s}</div>`).join("")}</div>`+table(["Status","When","Actor","Reason","Transaction"],d.history.map(x=>[esc(x.status),esc(fmtIST(x.created_at)),esc(x.actor),esc(x.reason||"—"),esc(x.txn_id||"—")]))+table(["Assigned unit","Group","Component","Expiry","Reservation"],d.units.map(x=>[esc(x.unit_id),esc(x.blood_group_id),esc(x.component_id),esc(x.expiry_date),esc(x.status)]));});};
$("requests").onclick=(e)=>{const a=e.target.closest("[data-track]");if(a){$("tracking-form").request_id.value=a.dataset.track;$("tracking-form").requestSubmit();}};
$("audit-filter").onclick=()=>{auditOffset=0;refreshAudit().catch(e=>toast(e.message,true));};$("audit-prev").onclick=()=>{auditOffset=Math.max(0,auditOffset-auditLimit);refreshAudit().catch(e=>toast(e.message,true));};$("audit-next").onclick=()=>{auditOffset+=auditLimit;refreshAudit().catch(e=>toast(e.message,true));};
async function loadHospitals(){
  try { const rows=await api("/api/hospitals"); $("hospital-list").innerHTML=table(["ID","Hospital","Location","Edit"],rows.map(h=>[esc(h.id),esc(h.name),esc(h.location),`<button class="btn btn-light hospital-edit" data-id="${esc(h.id)}" data-name="${esc(h.name)}" data-location="${esc(h.location)}">Edit</button>`])); }
  catch(e){ $("hospital-list").innerHTML=`<p class="hint">${esc(e.message)}</p>`; }
}
$("hospital-list").onclick=(e)=>{const b=e.target.closest(".hospital-edit");if(!b)return;const f=$("hospital-form");f.hospital_id.value=b.dataset.id;f.elements.namedItem("name").value=b.dataset.name;f.elements.namedItem("location").value=b.dataset.location;f.scrollIntoView({behavior:"smooth",block:"center"});};
$("hospital-clear").onclick=()=>$("hospital-form").reset();
$("hospital-form").onsubmit=(e)=>{e.preventDefault();const f=e.target,d={name:f.elements.namedItem("name").value.trim(),location:f.elements.namedItem("location").value.trim()},id=f.elements.namedItem("hospital_id").value;action(async()=>{const r=await api(id?`/api/hospitals/${id}`:"/api/hospitals",{method:id?"PATCH":"POST",body:JSON.stringify(d)});toast(id?"Hospital updated":"Hospital added");f.reset();await loadHospitals();await loadMeta();});};
$("login-open").onclick=()=>document.querySelector("#users-sec").scrollIntoView({behavior:"smooth"});
$("auth-form").onsubmit=(e)=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));action(async()=>{const r=await api("/api/auth/login",{method:"POST",body:JSON.stringify(d)});$("auth-status").textContent=`Signed in as ${r.user.username} · ${r.user.role}`;toast("Signed in");await loadUsers();});};
$("setup-admin").onclick=()=>{const d=Object.fromEntries(new FormData($("auth-form")));action(async()=>{await api("/api/auth/setup",{method:"POST",body:JSON.stringify(d)});toast("Admin created. Sign in now.");});};
$("signout").onclick=()=>action(async()=>{await api("/api/auth/logout",{method:"POST"});$("auth-status").textContent="Signed out.";});
async function loadUsers(){try{const d=await api("/api/users");$("users-list").innerHTML=table(["Username","Role","Hospital","Active"],d.map(u=>[esc(u.username),esc(u.role),esc(u.hospital_id??"—"),esc(u.active?"Yes":"No")]));}catch(e){$("users-list").innerHTML=`<p class="hint">${esc(e.message)}. Sign in as an admin to manage users.</p>`;}}
$("apply-report-filter").onclick=()=>{const q=queryString({from:$("report-from").value,to:$("report-to").value,group:$("report-group").value});document.querySelectorAll(".report-link").forEach(a=>{const base=a.href.split("?")[0];a.href=base+(q?"?"+q:"");});};
$("user-form").onsubmit=(e)=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));action(async()=>{await api("/api/users",{method:"POST",body:JSON.stringify(d)});toast("User created");e.target.reset();await loadUsers();});};
api("/api/auth/me").then(d=>{if(d.user){$("auth-status").textContent=`Signed in as ${d.user.username} · ${d.user.role}`;loadUsers();}}).catch(()=>{});
setInterval(()=>refresh(),30000);

/* ---------- OS simulation panels (every result comes from the C engine via Flask) ---------- */

const PALETTE = ["#2563eb", "#10b981", "#8b5cf6", "#ec4899", "#f59e0b", "#0ea5e9", "#ef4444", "#14b8a6"];

function osShow(id, html) {
  $(id).innerHTML = html;
}

function osError(id, msg) {
  osShow(id, `<div class="os-error">${esc(msg)}</div>`);
}

function osBusy(form, busy, label) {
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = busy;
  btn.textContent = busy ? "Running C engine…" : label;
}

// POST to an OS endpoint. Returns parsed JSON or throws a readable Error.
async function osPost(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

// runs one panel: busy state, error display, render callback
async function osRun(form, outId, label, build, render) {
  osBusy(form, true, label);
  try {
    const payload = build();
    osShow(outId, '<p class="hint">Running…</p>');
    const data = await osPost(form.dataset.endpoint, payload);
    osShow(outId, render(data));
  } catch (e) {
    osError(outId, e.message);
  } finally {
    osBusy(form, false, label);
  }
}

const numOr = (v, fallback) => (v === "" || v === null || v === undefined ? fallback : Number(v));

// ---- CPU scheduling ----
function parseProcessLines(text) {
  const rows = [];
  text.split("\n").map((l) => l.trim()).filter(Boolean).forEach((line, i) => {
    const parts = line.split(",").map((p) => p.trim());
    if (parts.length < 2) throw new Error(`process line ${i + 1}: use pid,arrival,burst[,priority]`);
    const row = { pid: parts[0], arrival: Number(parts[1]) };
    if (parts.length > 2) row.burst = Number(parts[2]);
    if (parts.length > 3) row.priority = Number(parts[3]);
    rows.push(row);
  });
  return rows;
}

function renderGantt(gantt) {
  const total = gantt.length ? gantt[gantt.length - 1].end - gantt[0].start : 0;
  if (!total) return "";
  const colours = {};
  let next = 0;
  const cells = gantt.map((g) => {
    const width = ((g.end - g.start) / total) * 100;
    const isIdle = g.pid === "IDLE";
    if (!isIdle && !colours[g.pid]) colours[g.pid] = PALETTE[next++ % PALETTE.length];
    const style = isIdle ? `width:${width}%` : `width:${width}%;background:${colours[g.pid]}`;
    return `<div class="${isIdle ? "idle" : ""}" style="${style}" title="${esc(g.pid)} ${g.start}-${g.end}">${esc(g.pid)}</div>`;
  }).join("");
  const scale = `<div class="gantt-scale"><span>${gantt[0].start}</span><span>${gantt[gantt.length - 1].end}</span></div>`;
  return `<div class="gantt">${cells}</div>${scale}`;
}

function renderCpu(d) {
  const a = d.averages;
  const kpi = (label, value) => `<div class="summary-box"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
  const rows = d.processes.map((p) => [esc(p.pid), esc(p.arrival), esc(p.burst), esc(p.priority), esc(p.completion),
    esc(p.turnaround), esc(p.waiting), esc(p.response)]);
  const idle = d.idle_intervals.length
    ? d.idle_intervals.map((i) => `${i.start}-${i.end}`).join(", ")
    : "none";
  return `
    <p><strong>${esc(d.algorithm)}</strong> finished at t=${esc(d.total_time)}. CPU idle: ${esc(d.idle_time)} tick(s) (${esc(idle)}).</p>
    <div class="os-kpis">
      ${kpi("Avg turnaround", a.turnaround)}${kpi("Avg waiting", a.waiting)}${kpi("Avg response", a.response)}
    </div>
    ${renderGantt(d.gantt)}
    <div style="margin-top:12px">${table(["PID", "AT", "BT", "Prio", "CT", "TAT", "WT", "RT"], rows)}</div>
    <p class="hint">Tie-breaking: ${esc(d.tie_breaking)}</p>`;
}

$("cpu-form").dataset.endpoint = "/api/os/scheduler";
$("cpu-form").onsubmit = (e) => {
  e.preventDefault();
  const f = e.target;
  osRun(f, "cpu-out", "Run scheduler", () => ({
    algorithm: f.algorithm.value,
    quantum: numOr(f.quantum.value, 1),
    processes: parseProcessLines(f.processes.value),
  }), renderCpu);
};

// ---- Banker's algorithm ----
const parseVector = (text) => text.split(",").map((x) => x.trim()).filter((x) => x !== "").map(Number);
const parseMatrix = (text) => text.split("\n").map((l) => l.trim()).filter(Boolean).map(parseVector);

function renderBanker(d) {
  const label = (i) => `P${i}`;
  let html = d.safe
    ? `<p>${badge("SAFE", "green")} Safe sequence: ${esc(d.safe_sequence.map(label).join(" → "))}</p>`
    : `<p>${badge("UNSAFE", "red")} No safe sequence exists for this state.</p>`;
  if (d.request) {
    const r = d.request;
    const kind = r.granted ? "green" : "orange";
    html += `<p>${badge(r.granted ? "GRANTED" : "DENIED", kind)} Request by ${esc(label(r.process))}: ${esc(r.reason)}.</p>`;
    if (r.granted) html += `<p class="hint">Safe sequence after granting: ${esc(r.safe_sequence_after.map(label).join(" → "))}</p>`;
  }
  html += table(["Process", "Need (per resource type)"], d.need.map((row, i) => [esc(label(i)), esc(row.join(", "))]));
  return html;
}

$("banker-form").dataset.endpoint = "/api/os/banker";
$("banker-form").onsubmit = (e) => {
  e.preventDefault();
  const f = e.target;
  osRun(f, "banker-out", "Check state", () => {
    const payload = {
      available: parseVector(f.available.value),
      max: parseMatrix(f.max.value),
      allocation: parseMatrix(f.allocation.value),
    };
    if (f.vector.value.trim() !== "") {
      payload.request = { process: numOr(f.process.value, 0), vector: parseVector(f.vector.value) };
    }
    return payload;
  }, renderBanker);
};

// ---- Deadlock detection ----
function renderDeadlock(d) {
  const notes = `<ul class="os-list">${d.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>`;
  if (!d.cycle_found) return `<p>${badge("NO CYCLE", "green")} The wait-for graph is acyclic. No deadlock.</p>${notes}`;
  const path = [...d.cycle, d.cycle[0]].join(" → ");
  const verdict = d.deadlock ? badge("DEADLOCK", "red") : badge("CYCLE, NOT CONFIRMED", "orange");
  return `<p>${verdict} Cycle: ${esc(path)}</p>
    <p>Victim to abort: <strong>${esc(d.victim)}</strong> (holds ${esc(d.victim_cost)} unit(s), the fewest in the cycle).</p>${notes}`;
}

$("deadlock-form").dataset.endpoint = "/api/os/deadlock";
$("deadlock-form").onsubmit = (e) => {
  e.preventDefault();
  const f = e.target;
  osRun(f, "deadlock-out", "Detect deadlock", () => {
    const edges = f.edges.value.split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
      const [from, to] = line.split(">").map((x) => (x || "").trim());
      return { from, to };
    });
    const held = {};
    f.held.value.split("\n").map((l) => l.trim()).filter(Boolean).forEach((line) => {
      const [k, v] = line.split("=").map((x) => (x || "").trim());
      held[k] = Number(v);
    });
    return {
      processes: f.processes.value.split(",").map((x) => x.trim()).filter(Boolean),
      edges,
      held,
      resource_model: f.resource_model.value,
    };
  }, renderDeadlock);
};

// ---- Producer-consumer ----
function renderPc(d) {
  const rows = [
    ["Items expected / produced / consumed", `${d.items_expected} / ${d.produced} / ${d.consumed}`],
    ["Exactly once (no loss, no duplicate)", d.exactly_once ? "yes" : "NO"],
    ["Checksums produced / consumed", `${d.checksum_produced} / ${d.checksum_consumed}`],
    ["Max items in buffer (capacity " + d.capacity + ")", d.max_occupancy],
    ["Producer waits on full buffer", d.producer_waits_full],
    ["Consumer waits on empty buffer", d.consumer_waits_empty],
    ["Duplicates / unknown items / order violations", `${d.duplicates} / ${d.unknown_items} / ${d.order_violations}`],
    ["Per producer / per consumer", `${d.per_producer.join(", ")} | ${d.per_consumer.join(", ")}`],
    ["Threads joined", d.threads_joined],
  ].map(([k, v]) => [esc(k), esc(v)]);
  return `<p>${d.exactly_once ? badge("CONSISTENT", "green") : badge("INCONSISTENT", "red")} Terminated cleanly.</p>
    ${table(["Measure", "Result"], rows)}
    <p class="hint">${esc(d.synchronization)}</p>`;
}

$("pc-form").dataset.endpoint = "/api/os/producer-consumer";
$("pc-form").onsubmit = (e) => {
  e.preventDefault();
  const f = e.target;
  osRun(f, "pc-out", "Run producers and consumers", () => ({
    capacity: numOr(f.capacity.value, null),
    producers: numOr(f.producers.value, null),
    consumers: numOr(f.consumers.value, null),
    items_per_producer: numOr(f.items_per_producer.value, null),
  }), renderPc);
};

// ---- Readers-writers ----
function renderRw(d) {
  const rows = [
    ["Reads completed / expected", `${d.reads_completed} / ${d.reads_expected}`],
    ["Writes completed / expected", `${d.writes_completed} / ${d.writes_expected}`],
    ["Max readers inside at once", d.max_concurrent_readers],
    ["Writer exclusion violations", d.writer_exclusion_violations],
    ["Torn reads", d.torn_reads],
    ["Final data version", d.final_version],
    ["Threads joined", d.threads_joined],
  ].map(([k, v]) => [esc(k), esc(v)]);
  return `<p>${d.all_operations_completed && d.writer_exclusion_violations === 0 && d.torn_reads === 0
    ? badge("CONSISTENT", "green") : badge("CHECK", "red")} All threads terminated.</p>
    ${table(["Measure", "Result"], rows)}
    <p class="hint">${esc(d.policy)}</p>`;
}

$("rw-form").dataset.endpoint = "/api/os/readers-writers";
$("rw-form").onsubmit = (e) => {
  e.preventDefault();
  const f = e.target;
  osRun(f, "rw-out", "Run readers and writers", () => ({
    readers: numOr(f.readers.value, null),
    writers: numOr(f.writers.value, null),
    reads_per_reader: numOr(f.reads_per_reader.value, null),
    writes_per_writer: numOr(f.writes_per_writer.value, null),
    hold_ms: numOr(f.hold_ms.value, 1),
  }), renderRw);
};

// engine availability banner
api("/api/os/status").then((s) => {
  $("os-engine-status").textContent = s.built
    ? "C engine: ready."
    : "C engine: not built yet. Run build.ps1 (Windows) or make (Linux), then restart the server.";
}).catch(() => {});

// Donor registry, screening, service pricing, and mapping utilities
async function loadDonors(){
 const rows=await api("/api/donors");
 $("donor-list").innerHTML=table(["Donor ID","Name","Phone","Group","Hospital","Registered"],rows.map(d=>[esc(d.donor_id),esc(d.full_name),esc(d.phone),esc(d.blood_group_id),esc(d.hospital),esc(d.created_at)]));
}
$("donor-form").onsubmit=(e)=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));action(async()=>{const r=await api("/api/donors",{method:"POST",body:JSON.stringify(d)});$("donor-token-result").textContent=`Donor ID ${r.donor_id} · Token ${r.token} · Registration service price ₹${r.service_price}`;e.target.reset();await loadDonors();});};
$("screening-form").onsubmit=(e)=>{e.preventDefault();const f=e.target,d=Object.fromEntries(new FormData(f)),id=d.donor_id;delete d.donor_id;action(async()=>{await api(`/api/donors/${id}/screening`,{method:"POST",body:JSON.stringify(d)});toast("Screening record saved");f.reset();});};
$("donor-refresh").onclick=()=>action(loadDonors);
async function loadPricing(){const rows=await api("/api/pricing");$("pricing-list").innerHTML=table(["Service","Key","Price (₹)","Updated"],rows.map(r=>[esc(r.display_name),esc(r.service_key),esc(Number(r.price).toFixed(2)),esc(r.updated_at)]));}
$("pricing-form").onsubmit=(e)=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));action(async()=>{await api(`/api/pricing/${d.service_key}`,{method:"PATCH",body:JSON.stringify({price:Number(d.price)})});await loadPricing();toast("Pricing updated");});};
api("/api/meta").then(m=>{const f=$("donor-form");fill(f.blood_group_id,m.groups.map(g=>[g,g]));fill(f.hospital_id,m.hospitals.map(h=>[h.id,h.name]));$("donor-mapping").innerHTML=table(["Recipient red-cell group","Common compatible donor groups (simplified)"],[ ["O−","O−"],["O+","O−, O+"],["A−","O−, A−"],["A+","O−, O+, A−, A+"],["B−","O−, B−"],["B+","O−, O+, B−, B+"],["AB−","O−, A−, B−, AB−"],["AB+","All ABO/Rh groups (red cells)" ]].map(r=>r.map(esc)));}).catch(()=>{});
api("/api/auth/me").then(({user})=>{if(user){loadDonors().catch(()=>{});loadPricing().catch(()=>{});}}).catch(()=>{});
