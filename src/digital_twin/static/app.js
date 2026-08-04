/*
  Single-column conversation controller.

  Research results render as photo-first cards and outreach actions only.
  Authority is still granted server-side by POST /confirm, so nothing here can
  push candidate context into the model on its own.
*/
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);

  const el = {
    bell: $("#bell"), bellDot: $("#bell-dot"),
    feed: $("#feed"), feedList: $("#feed-list"), feedClose: $("#feed-close"),
    intro: $("#intro"),
    visitorCard: $("#visitor-card"), visitorPhoto: $("#visitor-photo"),
    visitorInitials: $("#visitor-initials"), visitorNameOut: $("#visitor-name-out"),
    visitorRole: $("#visitor-role"), visitorLinks: $("#visitor-links"),
    sendEmail: $("#send-email"), openLinkedin: $("#open-linkedin"),
    people: $("#people"), peopleTitle: $("#people-title"), peopleGrid: $("#people-grid"),
    messages: $("#messages"), starters: $("#starters"),
    composer: $("#composer"), input: $("#composer-input"), send: $("#send-button"),
    contactLink: $("#contact-link"), modelNote: $("#model-note"),
    onboarding: $("#onboarding"), identityForm: $("#identity-form"),
    visitorName: $("#visitor-name"), visitorCompany: $("#visitor-company"),
    skipButton: $("#skip-button"),
    drawer: $("#drawer"), drawerTitle: $("#drawer-title"), drawerBody: $("#drawer-body"),
    drawerClose: $("#drawer-close"),
    projectsButton: $("#projects-button"), jdButton: $("#jd-button"),
    themeButton: $("#theme-button"), resetButton: $("#reset-button"),
    railBudget: $("#rail-budget"), railBudgetRow: $("#rail-budget-row"),
    toast: $("#toast"),
  };

  const state = {
    sessionId: null, events: null, candidates: [], active: null,
    drafts: [], busy: false, unread: 0,
    // The in-flight answer: the SSE tool events and the abort control both need
    // to reach the turn that is currently waiting.
    pending: null, controller: null,
    // Replayed into the thread after a reload so a refresh does not throw the
    // conversation away.
    transcript: [],
    calendar: null,
  };

  const STORE_KEY = "twin-session";

  // Deliberately answerable from the CV corpus. A suggested question that
  // triggers an honest refusal is a poor first impression of a grounded twin.
  const STARTERS = [
    "Give me the 60-second overview.",
    "What's his experience with Java and Spring Boot?",
    "Tell me about his work on AI agents.",
    "Is he a fit for a backend role?",
  ];

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const val = (f) => (f && typeof f === "object" && "value" in f ? f.value : f);

  const initialsOf = (n) => String(n || "?").split(/\s+/).filter(Boolean).slice(0, 2)
    .map((p) => p[0].toUpperCase()).join("");

  const secs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);

  /*
    Answers arrive as plain text that frequently contains paragraphs and dashed
    lists. Rendered with white-space: pre-wrap they collapsed into one grey slab,
    so the structure the model actually produced is reconstructed here. The input
    is escaped first: only the tags added below can reach the DOM.
  */
  function format(text) {
    const bold = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    return String(text || "")
      .split(/\n{2,}/)
      .map((block) => {
        const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);
        const bulleted = lines.length > 1 && lines.every((l) => /^[-*•]\s+/.test(l));
        const numbered = lines.length > 1 && lines.every((l) => /^\d+[.)]\s+/.test(l));
        if (bulleted || numbered) {
          const tag = numbered ? "ol" : "ul";
          const items = lines
            .map((l) => `<li>${bold(esc(l.replace(/^([-*•]|\d+[.)])\s+/, "")))}</li>`)
            .join("");
          return `<${tag}>${items}</${tag}>`;
        }
        return `<p>${bold(esc(block.trim())).replace(/\n/g, "<br>")}</p>`;
      })
      .join("");
  }

  /*
    A step in the agent's tool loop. The same markup serves the live view and the
    provenance panel kept underneath the finished answer, so what the visitor
    watched happen is exactly what stays on the record.
  */
  function stepRow(step) {
    const status = step.status || "running";
    // "ok" would only repeat what the marker already says; anything else is
    // worth naming, because a blocked or timed-out call changes how much the
    // answer rests on.
    const meta = [
      status === "running" || status === "ok" ? null : status,
      typeof step.duration_ms === "number" ? secs(step.duration_ms) : null,
      step.cached ? "cached" : null,
    ].filter(Boolean).join(" · ");
    // The tool reports one URL per result; several usually share a host, and
    // three repetitions of the same domain says nothing extra.
    const hosts = new Map();
    (step.source_urls || []).forEach((u) => {
      let host = u;
      try { host = new URL(u).hostname.replace(/^www\./, ""); } catch { /* raw */ }
      if (!hosts.has(host)) hosts.set(host, u);
    });
    const links = [...hosts].slice(0, 3).map(([host, u]) =>
      `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(host)}</a>`).join("");
    return `
      <li class="step" data-seq="${esc(step.sequence)}" data-status="${esc(status)}">
        <span class="step-mark" aria-hidden="true"></span>
        <span class="step-body">
          <span class="step-phrase">${esc(step.phrase || step.tool || "Working")}</span>
          ${step.summary ? `<span class="step-summary">${esc(step.summary)}</span>` : ""}
          ${links ? `<span class="step-links">${links}</span>` : ""}
        </span>
        ${meta ? `<span class="step-meta">${esc(meta)}</span>` : ""}
      </li>`;
  }

  const stepRows = (steps) => steps.map(stepRow).join("");
  const stepsList = (steps) => `<ol class="steps">${stepRows(steps)}</ol>`;

  let toastTimer;
  function toast(msg) {
    el.toast.textContent = msg;
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.toast.hidden = true), 3800);
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      ...options,
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `Request failed (${res.status})`);
    }
    return res.status === 204 ? null : res.json();
  }

  /* ---------- activity feed ---------- */

  function note(text) {
    const li = document.createElement("li");
    const t = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    li.innerHTML = `<time>${esc(t)}</time><span>${esc(text)}</span>`;
    el.feedList.prepend(li);
    state.unread += 1;
    el.bellDot.hidden = false;
  }

  el.bell.addEventListener("click", () => {
    el.feed.hidden = !el.feed.hidden;
    if (!el.feed.hidden) { state.unread = 0; el.bellDot.hidden = true; }
  });
  el.feedClose.addEventListener("click", () => (el.feed.hidden = true));

  /* ---------- people ---------- */

  function renderPeople(candidates) {
    state.candidates = candidates;
    if (!candidates.length) { el.people.hidden = true; return; }
    el.people.hidden = false;
    el.peopleTitle.textContent =
      candidates.length === 1 ? "Is this you?" : "Which one is you?";
    $("#people-sub").textContent =
      candidates.length === 1
        ? "Found one public profile that looks like you."
        : `Found ${candidates.length} public profiles with that name.`;

    el.peopleGrid.innerHTML = candidates.map((c, i) => {
      const photo = c.avatar?.url || c.photo_url;
      const ini = c.avatar?.initials || c.initials || initialsOf(c.name);
      // A short human line beats a raw score: say what was actually observed.
      const desc = val(c.bio)
        || (Array.isArray(c.why) ? c.why.slice(0, 2).join(" · ") : c.why)
        || "";
      const where = [val(c.company_detail) || c.company, val(c.location)]
        .filter(Boolean).join(" · ");
      return `
        <article class="person" data-pick="${i}" role="button" tabindex="0"
                 aria-label="Select ${esc(c.name)}">
          ${photo
            ? `<img src="${esc(photo)}" alt="" loading="lazy"
                 onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'initials',textContent:'${esc(ini)}'}))">`
            : `<span class="initials">${esc(ini)}</span>`}
          <strong>${esc(c.name)}</strong>
          ${c.headline ? `<span class="role">${esc(c.headline)}</span>` : ""}
          ${where ? `<span class="role">${esc(where)}</span>` : ""}
          ${desc ? `<p class="desc">${esc(desc)}</p>` : ""}
          ${c.source_label ? `<span class="src">via ${esc(c.source_label)}</span>` : ""}
          ${c.confidence ? `<span class="pct">${esc(c.confidence)}% match</span>` : ""}
          <button type="button" class="btn sm pick" data-pick="${i}">That's me</button>
        </article>`;
    }).join("");
  }

  function pick(index) {
    const candidate = state.candidates[index];
    if (!candidate) return;
    api(`/api/sessions/${state.sessionId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ candidate_id: candidate.id }),
    }).then((result) => {
      el.people.hidden = true;
      showVisitor(result);
      note(`Confirmed ${candidate.name}`);
      toast("Thanks — I'll tailor what I show you.");
    }).catch((e) => toast(e.message));
  }

  el.peopleGrid.addEventListener("click", (e) => {
    const card = e.target.closest("[data-pick]");
    if (card) pick(Number(card.dataset.pick));
  });
  el.peopleGrid.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const card = e.target.closest("[data-pick]");
    if (card) { e.preventDefault(); pick(Number(card.dataset.pick)); }
  });

  /* ---------- visitor card ---------- */

  function showVisitor(payload) {
    const c = payload.candidate || payload;
    state.active = c;
    state.drafts = payload.outreach?.drafts || payload.drafts || [];

    el.visitorCard.hidden = false;
    el.visitorNameOut.textContent = c.name || "";
    el.visitorRole.textContent =
      val(c.role) || c.headline || val(c.company_detail) || c.company || "";

    const photo = c.avatar?.url || c.photo_url;
    if (photo) {
      el.visitorPhoto.src = photo;
      el.visitorPhoto.hidden = false;
      el.visitorInitials.hidden = true;
      el.visitorPhoto.onerror = () => {
        el.visitorPhoto.hidden = true;
        el.visitorInitials.hidden = false;
      };
    } else {
      el.visitorPhoto.hidden = true;
      el.visitorInitials.hidden = false;
    }
    el.visitorInitials.textContent = c.avatar?.initials || initialsOf(c.name);

    const links = (c.profiles || []).map((p) =>
      `<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(
        p.kind.replace(/_/g, " "))}</a>`);
    if (c.email?.address) links.push(`<span class="chip">${esc(c.email.address)}</span>`);
    el.visitorLinks.innerHTML = links.join("");

    el.sendEmail.hidden = !c.email?.address;
    el.openLinkedin.hidden = !(c.profiles || []).some((p) => p.kind === "linkedin");
  }

  el.sendEmail.addEventListener("click", () => {
    const draft = state.drafts[0];
    const variant = draft?.variants?.[0];
    const to = state.active?.email?.address || draft?.recipient || "";
    if (!to) return;
    window.open(
      `mailto:${to}?subject=${encodeURIComponent(variant?.subject || "")}` +
      `&body=${encodeURIComponent(variant?.body || "")}`, "_blank");
    note(`Opened an email to ${to}`);
  });

  el.openLinkedin.addEventListener("click", () => {
    const p = (state.active?.profiles || []).find((x) => x.kind === "linkedin");
    if (p) window.open(p.url, "_blank", "noopener");
  });

  /* ---------- chat ---------- */

  let replaying = false;

  // The claim status is the whole point of a grounded twin, so it is stated on
  // the turn rather than left for the visitor to infer from the wording.
  function badges(extra) {
    const out = [];
    // The two refusals are not the same thing and must not read the same. A
    // contractual question is declined on policy and still cites the boundary
    // it was declined under; an unevidenced one is refused for lack of proof.
    if (extra.refusal) {
      out.push(extra.grounded
        ? '<span class="badge warn">Not the twin\'s to answer</span>'
        : '<span class="badge warn">No evidence for this</span>');
    } else if (extra.grounded) {
      out.push('<span class="badge ok">Grounded in sources</span>');
    }
    if (extra.tailored_for) {
      out.push(`<span class="badge">Tailored for ${esc(extra.tailored_for)}</span>`);
    }
    return out.length ? `<div class="badges">${out.join("")}</div>` : "";
  }

  function turn(role, text, extra = {}) {
    const div = document.createElement("div");
    div.className = `turn ${role}`;
    const sources = extra.sources || [];
    const trace = extra.trace || [];
    const chips = sources.map((c) => `<span class="cite">${esc(c)}</span>`).join("");
    const spent = trace.reduce((total, s) => total + (s.duration_ms || 0), 0);
    const calls = `${trace.length} tool ${trace.length === 1 ? "call" : "calls"}`;
    // Every tool call the agent actually made stays attached to the answer it
    // produced. Collapsed by default: it is provenance, not the answer.
    const provenance = trace.length
      ? `<details class="trace">
           <summary>How this was assembled · ${calls} · ${secs(spent)}</summary>
           ${stepsList(trace)}
         </details>`
      : "";
    div.innerHTML = `
      <div class="bubble">
        <span class="label">${role === "twin" ? "Prathamesh" : "You"}</span>
        <div class="text">${role === "twin" ? format(text) : esc(text)}</div>
        ${role === "twin" && text ? badges(extra) : ""}
        ${chips ? `<div class="cites">${chips}</div>` : ""}
        ${provenance}
        ${role === "twin" && text
          ? `<div class="turn-actions">
               <button type="button" data-copy>Copy</button>
               ${sources.length ? '<button type="button" data-copy-cited>Copy with sources</button>' : ""}
             </div>`
          : ""}
      </div>`;
    el.messages.appendChild(div);
    // "nearest" only scrolls when the turn is actually off-screen. Aligning to
    // "end" threw the page down past the conversation into the sections below,
    // so the answer landed above the fold and the chat looked like it had done
    // nothing at all. Replaying a stored transcript scrolls nowhere: the visitor
    // should land where they landed on any other page load.
    if (!replaying) div.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return div;
  }

  /* ---------- transcript persistence ---------- */

  // A reload used to discard the conversation while the server still held the
  // history, so the visitor's own screen disagreed with the session behind it.
  function record(role, text, extra) {
    state.transcript.push({ role, text, extra: extra || {} });
    try {
      sessionStorage.setItem(STORE_KEY, JSON.stringify({
        sessionId: state.sessionId, turns: state.transcript.slice(-40),
      }));
    } catch { /* private mode: the conversation simply does not survive reload */ }
  }

  function forgetLocally() {
    state.transcript = [];
    try { sessionStorage.removeItem(STORE_KEY); } catch { /* nothing stored */ }
  }

  /* ---------- asking ---------- */

  function setSending(sending) {
    el.send.dataset.mode = sending ? "stop" : "send";
    el.send.setAttribute("aria-label", sending ? "Stop" : "Send");
  }

  function showBudget(response) {
    if (typeof response.budget_remaining !== "number" || !el.railBudget) return;
    el.railBudgetRow.hidden = false;
    el.railBudget.textContent = `${response.budget_remaining.toLocaleString()} tokens`;
  }

  async function ask(text) {
    text = String(text || "").trim();
    if (!text || state.busy || !state.sessionId) return;
    state.busy = true;
    setSending(true);
    if (el.intro) el.intro.hidden = true;
    // Recorded here rather than in the suggestion handler, so a question the
    // visitor typed also drops out of the follow-ups.
    asked.add(text);
    turn("you", text);
    record("you", text);
    el.input.value = "";
    el.input.style.height = "auto";

    // A grounded answer takes several seconds. Silent dots are indistinguishable
    // from a broken page, so the live tool loop is shown as it runs; the timed
    // copy below is only the fallback for the retrieval-only path, where no tool
    // events are emitted at all.
    const pending = turn("twin", "");
    const slot = pending.querySelector(".text");
    slot.innerHTML =
      '<span class="waiting"><span class="typing"><i></i><i></i><i></i></span>' +
      '<span class="waiting-label">Retrieving evidence from his CV and repositories…</span>' +
      '<span class="waiting-clock">0s</span></span>' +
      '<ol class="steps live"></ol>';
    const startedAt = Date.now();
    const clock = slot.querySelector(".waiting-clock");
    const label = slot.querySelector(".waiting-label");
    state.pending = { steps: [], host: slot.querySelector(".steps"), label, tooled: false };
    const ticker = setInterval(() => {
      const elapsed = Math.round((Date.now() - startedAt) / 1000);
      clock.textContent = `${elapsed}s`;
      if (state.pending?.tooled) return;
      if (elapsed === 4) label.textContent = "Drafting a grounded answer…";
      if (elapsed === 12) label.textContent = "Still working — verifying every claim…";
    }, 1000);

    const controller = new AbortController();
    state.controller = controller;
    // Purging mid-answer aborts this request and clears the thread. The turn it
    // was going to append belongs to a session that no longer exists, so the
    // session id is captured here and every write below is gated on it.
    const sessionId = state.sessionId;
    const current = () => state.sessionId === sessionId;
    try {
      const r = await api(`/api/sessions/${sessionId}/chat`, {
        method: "POST",
        body: JSON.stringify({ message: text }),
        signal: controller.signal,
      });
      pending.remove();
      if (!current()) return;
      const extra = {
        sources: r.sources, grounded: r.grounded, refusal: r.refusal,
        tailored_for: r.tailored_for, trace: r.trace,
      };
      turn("twin", r.answer, extra);
      record("twin", r.answer, extra);
      showBudget(r);
      offerFollowUps();
    } catch (e) {
      pending.remove();
      if (!current()) return;
      // Aborting only stops this browser waiting; the server finishes the turn
      // it already started, so the wording promises nothing more than that.
      const message = e.name === "AbortError"
        ? "Stopped waiting for that answer. Ask again whenever you like."
        : `Sorry — ${e.message}`;
      turn("twin", message);
      record("twin", message);
    } finally {
      clearInterval(ticker);
      state.pending = null;
      state.controller = null;
      state.busy = false;
      setSending(false);
      if (current()) {
        el.resetButton.hidden = false;
        // preventScroll matters: refocusing the composer otherwise drags the
        // viewport down to it, past the answer that just arrived.
        el.input.focus({ preventScroll: true });
      }
    }
  }

  el.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.busy) { state.controller?.abort(); return; }
    ask(el.input.value);
  });
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(el.input.value); }
  });
  el.input.addEventListener("input", () => {
    el.input.style.height = "auto";
    el.input.style.height = `${Math.min(el.input.scrollHeight, 170)}px`;
  });

  /* ---------- suggestions ---------- */

  // Every follow-up is answerable from the CV corpus, and asked questions drop
  // out of the list so the rail never suggests something already answered.
  const FOLLOW_UPS = [
    "What did he actually ship at matriXploit?",
    "How does he handle failures in a tool loop?",
    "What has he built with retrieval and embeddings?",
    "Where is his security work strongest?",
    "What is he weakest at?",
    "Which of his repositories should I read first?",
    "How does he test the systems he builds?",
    "What is he looking for in his next role?",
  ];
  const asked = new Set();

  // Not a question: this chip opens the role-fit drawer, which is the thing a
  // recruiter came to do.
  const JD_CHIP = "Check him against a job description";

  function renderSuggestions(items) {
    el.starters.innerHTML = items.map((s) =>
      `<button type="button"${s === JD_CHIP ? ' data-action="jd"' : ""}>${esc(s)}</button>`,
    ).join("");
  }

  function offerFollowUps() {
    renderSuggestions([...FOLLOW_UPS.filter((q) => !asked.has(q)).slice(0, 3), JD_CHIP]);
  }

  renderSuggestions(STARTERS);
  el.starters.addEventListener("click", (e) => {
    if (e.target.tagName !== "BUTTON") return;
    if (e.target.dataset.action === "jd") { el.jdButton.click(); return; }
    ask(e.target.textContent);
  });

  /* ---------- events ---------- */

  function openEvents() {
    const src = new EventSource(`/api/sessions/${state.sessionId}/events`);
    state.events = src;

    src.addEventListener("research", (e) => {
      const p = JSON.parse(e.data);
      if (p.status === "researching") note(`Looking up ${p.name || "your name"}`);
      if (p.status === "candidates") {
        renderPeople(p.candidates || []);
        note(`Found ${(p.candidates || []).length} possible match(es)`);
      }
      if (p.status === "empty") note("No public match found");
    });

    src.addEventListener("research.dossier", (e) => {
      const p = JSON.parse(e.data);
      if (p.candidates?.length) renderPeople(p.candidates);
    });

    /*
      The agent publishes every tool call and result as it happens. Rendering
      them live is what makes the execution contract visible: the visitor sees
      which public sources were touched, in order, while they wait — and the
      same list stays attached to the finished answer.
    */
    src.addEventListener("tool.call", (e) => {
      const p = JSON.parse(e.data);
      const live = state.pending;
      if (!live) return;
      live.tooled = true;
      // The step list below spells out each call, so the headline stays the
      // headline rather than repeating the last row verbatim.
      live.label.textContent = "Consulting public sources…";
      live.steps.push({ ...p, status: "running" });
      live.host.innerHTML = stepRows(live.steps);
    });

    src.addEventListener("tool.result", (e) => {
      const p = JSON.parse(e.data);
      const live = state.pending;
      if (!live) return;
      const step = live.steps.find((s) => s.call_id === p.call_id);
      if (step) Object.assign(step, p);
      live.host.innerHTML = stepRows(live.steps);
      if (p.status && p.status !== "ok") note(`${p.tool}: ${p.status}`);
    });

    src.addEventListener("outreach.ready", (e) => {
      const p = JSON.parse(e.data);
      state.drafts = p.drafts || [];
      if (state.drafts.length) note("Outreach draft ready");
    });

    src.addEventListener("outreach.action", (e) => {
      const p = JSON.parse(e.data);
      if (p.status === "sent") note("Email sent");
      else if (p.reason) note(`Email not sent: ${p.reason}`);
    });

    src.onerror = () => { /* EventSource retries; chat must keep working. */ };
  }

  /* ---------- onboarding ---------- */

  el.identityForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = el.visitorName.value.trim();
    const company = el.visitorCompany.value.trim();
    el.onboarding.hidden = true;
    if (!name) return;
    try {
      await api(`/api/sessions/${state.sessionId}/identity`, {
        method: "POST", body: JSON.stringify({ name, company: company || null }),
      });
    } catch (err) { toast(err.message); }
  });

  el.skipButton.addEventListener("click", async () => {
    el.onboarding.hidden = true;
    try { await api(`/api/sessions/${state.sessionId}/skip`, { method: "POST" }); } catch {}
  });

  /* ---------- drawer ---------- */

  function openDrawer(title, html) {
    el.drawerTitle.textContent = title;
    el.drawerBody.innerHTML = html;
    el.drawer.hidden = false;
  }
  el.drawerClose.addEventListener("click", () => (el.drawer.hidden = true));
  el.drawer.addEventListener("click", (e) => { if (e.target === el.drawer) el.drawer.hidden = true; });

  // Optional: in the current layout Work is a section link, not a drawer
  // trigger, so this button may not exist.
  el.projectsButton?.addEventListener("click", async () => {
    openDrawer("Selected work", "<p>Loading…</p>");
    try {
      const d = await api("/api/github");
      const repos = d.repositories || d.repos || [];
      openDrawer("Selected work", repos.map((r) => `
        <div class="repo">
          <strong><a href="${esc(r.url || r.html_url)}" target="_blank" rel="noopener noreferrer">${esc(r.name)}</a></strong>
          <p>${esc(r.description || "")}</p>
        </div>`).join(""));
    } catch (e) { openDrawer("Selected work", `<p>${esc(e.message)}</p>`); }
  });

  /*
    Job-description fit. The response fields are coverage_percent, matched
    (requirement/evidence/source), not_evidenced, summary and caveat — the gap
    list was previously read from a field name the API never returned, so the
    honest half of the analysis never reached the screen.
  */
  function renderFit(fit) {
    const matched = fit.matched || [];
    const gaps = fit.not_evidenced || [];
    const pct = Math.max(0, Math.min(100, Number(fit.coverage_percent) || 0));
    return `
      <div class="fit-head">
        <!-- The summary beside it already names the ratio in words, so the ring
             carries the number alone rather than a caption it cannot fit. -->
        <div class="fit-meter" data-pct="${pct}" role="img"
             aria-label="${pct}% of recognised requirements are directly evidenced">
          <strong>${pct}%</strong>
        </div>
        <p class="fit-summary">${esc(fit.summary || "")}</p>
      </div>
      <div class="fit-group">
        <h3>Evidenced <span>${matched.length}</span></h3>
        ${matched.length ? matched.map((m) => `
          <div class="fit-row ok">
            <strong>${esc(m.requirement || "")}</strong>
            ${m.evidence ? `<p>${esc(m.evidence)}</p>` : ""}
            ${m.source ? `<span class="cite">${esc(m.source)}</span>` : ""}
          </div>`).join("") : "<p class='fit-empty'>Nothing in this description matched the CV directly.</p>"}
      </div>
      <div class="fit-group">
        <h3>Not evidenced here <span>${gaps.length}</span></h3>
        ${gaps.length ? gaps.map((g) => `
          <div class="fit-row gap">
            <strong>${esc(g.requirement || g)}</strong>
            <p>Not stated in this CV. The twin will not claim it.</p>
          </div>`).join("") : "<p class='fit-empty'>Every requirement it could parse is evidenced.</p>"}
      </div>
      ${fit.caveat ? `<p class="fit-caveat">${esc(fit.caveat)}</p>` : ""}
      <div class="sheet-actions"><button type="button" class="btn ghost" id="fit-copy">Copy this analysis</button></div>`;
  }

  el.jdButton.addEventListener("click", () => {
    openDrawer("Role fit", `
      <p class="over-lede">Paste a job description. Requirements are split into
        directly evidenced and not evidenced — never quietly upgraded.</p>
      <form class="jd-form" id="jd-form">
        <textarea id="jd-input" placeholder="Paste the job description…"></textarea>
        <div class="sheet-actions"><button type="submit" class="btn">Check fit</button></div>
      </form><div id="jd-results"></div>`);
    $("#jd-input").focus();
    $("#jd-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const description = $("#jd-input").value.trim();
      if (description.length < 20) { toast("Paste a little more of the description."); return; }
      const out = $("#jd-results");
      out.innerHTML = "<p>Checking every requirement against the CV…</p>";
      try {
        const fit = await api(`/api/sessions/${state.sessionId}/jd-fit`, {
          method: "POST", body: JSON.stringify({ description }),
        });
        out.innerHTML = renderFit(fit);
        // The app sends style-src 'self', so the coverage ring cannot carry an
        // inline style attribute. CSSOM is not inline style, and is allowed.
        const meter = $(".fit-meter", out);
        meter?.style.setProperty("--pct", meter.dataset.pct);
        $("#fit-copy").addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(out.innerText.trim());
            toast("Analysis copied.");
          } catch { toast("Your browser blocked clipboard access."); }
        });
      } catch (err) { out.innerHTML = `<p>${esc(err.message)}</p>`; }
    });
  });

  /* ---------- contact ---------- */

  const contactSheet = $("#contact-sheet");

  async function openContact() {
    const rows = $("#contact-rows");
    rows.innerHTML = "<p>Loading…</p>";
    contactSheet.hidden = false;
    const items = [];
    // Only offered when the owner has actually configured a booking link.
    if (state.calendar?.url) {
      items.push([state.calendar.url, "Book a call", state.calendar.cta || "Find a time"]);
    }
    try {
      const c = await api("/api/contact");
      if (c.email) items.push([`mailto:${c.email}`, "Email", c.email]);
      if (c.phone) items.push([`tel:${c.phone}`, "Phone", c.phone]);
      if (c.location) items.push(["", "Based in", c.location]);
    } catch { /* fall through to the static links below */ }
    // Always offered, independent of whether the contact endpoint responded.
    items.push(["https://www.linkedin.com/in/prathameshkalamkar", "LinkedIn", "prathameshkalamkar"]);
    items.push(["https://github.com/prathamesh-git9", "GitHub", "prathamesh-git9"]);
    rows.innerHTML = items.map(([href, label, value]) =>
      href
        ? `<a class="contact-row" href="${esc(href)}"${href.startsWith("http") ? ' target="_blank" rel="noopener noreferrer"' : ""}>
             <div><span>${esc(label)}</span><strong>${esc(value)}</strong></div></a>`
        : `<div class="contact-row"><div><span>${esc(label)}</span><strong>${esc(value)}</strong></div></div>`,
    ).join("");
  }

  $("#contact-button").addEventListener("click", openContact);
  $("#rail-contact")?.addEventListener("click", openContact);
  $("#contact-close").addEventListener("click", () => (contactSheet.hidden = true));
  contactSheet.addEventListener("click", (e) => {
    if (e.target.dataset.close !== undefined) contactSheet.hidden = true;
  });
  // Optional hero shortcuts: present in some layouts, absent in others.
  $("#hero-contact")?.addEventListener("click", openContact);
  $("#hero-work")?.addEventListener("click", () => el.projectsButton?.click());

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    contactSheet.hidden = true;
    el.drawer.hidden = true;
    el.feed.hidden = true;
  });

  /* ---------- theme ---------- */

  const saved = localStorage.getItem("twin-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  el.themeButton.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("twin-theme", next);
  });

  /* ---------- session lifecycle ---------- */

  // GET /calendar only needs the session to exist, so it doubles as the cheapest
  // liveness probe for a stored session id — and tells us whether there is a
  // booking link worth offering.
  async function probeSession(sessionId) {
    try {
      const calendar = await api(`/api/sessions/${sessionId}/calendar`);
      state.calendar = calendar.configured && calendar.url ? calendar : null;
      return true;
    } catch {
      return false;
    }
  }

  async function restoreSession() {
    let stored = null;
    try { stored = JSON.parse(sessionStorage.getItem(STORE_KEY) || "null"); } catch { /* corrupt */ }
    if (!stored?.sessionId || !(await probeSession(stored.sessionId))) {
      forgetLocally();
      return false;
    }
    state.sessionId = stored.sessionId;
    state.transcript = Array.isArray(stored.turns) ? stored.turns : [];
    if (!state.transcript.length) return true;

    replaying = true;
    state.transcript.forEach((t) => turn(t.role, t.text, t.extra || {}));
    replaying = false;
    state.transcript.filter((t) => t.role === "you").forEach((t) => asked.add(t.text));
    if (el.intro) el.intro.hidden = true;
    el.resetButton.hidden = false;
    offerFollowUps();
    return true;
  }

  async function newSession() {
    const s = await api("/api/sessions", { method: "POST", body: "{}" });
    state.sessionId = s.session_id;
    forgetLocally();
    await probeSession(state.sessionId);
    return s;
  }

  // Stop and purge, as promised: the server deletes the visit, its research and
  // its messages, and the browser drops its own copy of the transcript.
  el.resetButton.addEventListener("click", async () => {
    const sessionId = state.sessionId;
    if (!sessionId) return;
    // Cleared first: an answer still in flight checks this before writing itself
    // into a thread the visitor has just asked to be rid of.
    state.sessionId = null;
    state.controller?.abort();
    el.resetButton.disabled = true;
    try {
      await api(`/api/sessions/${sessionId}`, { method: "DELETE" });
    } catch (e) {
      toast(e.message);
    }
    state.events?.close();
    el.messages.innerHTML = "";
    el.visitorCard.hidden = true;
    el.people.hidden = true;
    el.feedList.innerHTML = "";
    el.bellDot.hidden = true;
    state.unread = 0;
    el.railBudgetRow.hidden = true;
    asked.clear();
    forgetLocally();
    if (el.intro) el.intro.hidden = false;
    renderSuggestions(STARTERS);
    try {
      await newSession();
      openEvents();
      toast("Session purged. Starting fresh.");
    } catch (e) {
      toast(e.message);
    }
    el.resetButton.disabled = false;
    el.resetButton.hidden = true;
  });

  // Recruiters live on the keyboard: the composer is one key away from anywhere
  // on the page.
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || "");
    const shortcut = (e.key === "/" && !typing)
      || ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k");
    if (!shortcut) return;
    e.preventDefault();
    el.input.scrollIntoView({ behavior: "smooth", block: "center" });
    el.input.focus({ preventScroll: true });
  });

  /* ---------- landing sections ---------- */

  $("#cta-ask")?.addEventListener("click", () => {
    el.input.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => el.input.focus({ preventScroll: true }), 500);
  });
  $("#cta-contact")?.addEventListener("click", () => $("#contact-button").click());

  $("#hero-start")?.addEventListener("click", () => {
    el.input.scrollIntoView({ behavior: "smooth", block: "center" });
    el.input.focus({ preventScroll: true });
  });

  // The work section is filled from live repository data rather than a
  // hand-maintained list, so it cannot drift from what is actually published.
  async function loadWorkCards() {
    const host = $("#work-cards");
    if (!host) return;
    try {
      const data = await api("/api/github");
      const repos = data.repositories || data.repos || [];
      if (!repos.length) return;
      // GitHub allows 60 unauthenticated calls an hour per IP, which a shared
      // host exhausts routinely. The service fills the gap with a placeholder
      // description; printing that ten times reads as ten broken cards, so a
      // repository without live metadata is shown as a plain link and the
      // section says once why the detail is missing.
      const live = repos.filter((r) => r.live !== false).length;
      host.innerHTML = repos.map((r) => {
        const facts = [r.language, r.stars ? `${r.stars}★` : null].filter(Boolean);
        return `
        <article class="card${r.live === false ? " bare" : ""}">
          <h3><a href="${esc(r.url || r.html_url)}" target="_blank" rel="noopener noreferrer">${esc(r.name)}</a></h3>
          ${r.live === false ? "" : `<p>${esc(r.description || "")}</p>`}
          ${facts.length ? `<div class="facts">${facts.map((f) => `<span>${esc(f)}</span>`).join("")}</div>` : ""}
          ${r.topics?.length
            ? `<div class="topics">${r.topics.slice(0, 3)
                .map((t) => `<span>${esc(t)}</span>`).join("")}</div>`
            : ""}
        </article>`;
      }).join("");
      const note = $("#work-note");
      if (note && !live) {
        note.textContent =
          "GitHub's public API is rate-limited at the moment, so the live detail "
          + "is missing. Every name links straight to the repository.";
        note.hidden = false;
      }
    } catch {
      host.closest(".band")?.remove();
    }
  }

  /* ---------- scroll progress ---------- */

  const progress = $("#progress");
  const bar = document.querySelector(".bar");
  const paint = () => {
    if (progress) {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const pct = max > 0 ? (window.scrollY / max) * 100 : 0;
      progress.style.width = `${pct}%`;
    }
    // Frosted glass is right over the sky at the top of the page and wrong
    // everywhere else: body copy scrolling under a blurred translucent bar came
    // out smeared and sliced mid-line. Past the first scroll it goes solid.
    bar?.classList.toggle("solid", window.scrollY > 8);
  };
  addEventListener("scroll", paint, { passive: true });
  addEventListener("resize", paint);
  paint();

  /* ---------- copy an answer ---------- */

  // Recruiters paste answers into their notes or ATS; making that one click
  // keeps the twin's exact wording intact rather than a lossy manual selection.
  el.messages.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy], [data-copy-cited]");
    if (!button) return;
    const bubble = button.closest(".turn");
    const answer = bubble?.querySelector(".text")?.innerText.trim() || "";
    const withSources = button.hasAttribute("data-copy-cited");
    // Pasting an answer into an ATS without its sources strips exactly the part
    // that makes it checkable, so citations travel with it on request.
    const cited = [...(bubble?.querySelectorAll(".cite") || [])]
      .map((n) => `- ${n.textContent}`).join("\n");
    const payload = withSources && cited ? `${answer}\n\nSources:\n${cited}` : answer;
    const label = button.textContent;
    try {
      await navigator.clipboard.writeText(payload);
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = label), 1800);
    } catch {
      toast("Your browser blocked clipboard access.");
    }
  });

  /* ---------- scroll reveal ---------- */

  // Sections ease in once as they enter view, then stop being observed so
  // scrolling back up does not replay the animation.
  function armReveals() {
    const items = document.querySelectorAll(".reveal");
    if (!("IntersectionObserver" in window)) {
      items.forEach((n) => n.classList.add("in"));
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("in");
        io.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -12% 0px", threshold: 0.12 });
    items.forEach((n) => io.observe(n));
  }

  // Stagger the timeline and card grids so they cascade rather than snap.
  function stagger(selector, step = 70) {
    document.querySelectorAll(selector).forEach((n, i) => {
      n.style.transitionDelay = `${i * step}ms`;
    });
  }

  /* ---------- boot ---------- */

  (async function boot() {
    try {
      const h = await api("/api/health");
      if (h.model) el.modelNote.textContent = h.model;
      const railModel = $("#rail-model");
      if (railModel && h.model) railModel.textContent = h.model;
    } catch {}

    try {
      const c = await api("/api/contact");
      // Optional: contact now lives in the slide-over, so the inline link may
      // not be present in this layout.
      if (c.email && el.contactLink) el.contactLink.href = `mailto:${c.email}`;
      else el.contactLink?.remove();
    } catch { el.contactLink?.remove(); }

    loadWorkCards();
    stagger(".timeline .reveal", 80);
    armReveals();

    try {
      // A reload resumes the same session and thread where possible. Only a
      // genuinely new visit is worth interrupting with the identity prompt.
      const resumed = await restoreSession();
      if (!resumed) await newSession();
      // No greeting turn: the opening line above the input already says what
      // the twin is and what it can be asked, and repeating it reads as a bug.
      openEvents();
      if (!resumed) {
        el.onboarding.hidden = false;
        el.visitorName.focus();
      }
    } catch (e) {
      turn("twin", `Couldn't start a session: ${e.message}`);
    }
  })();
})();
