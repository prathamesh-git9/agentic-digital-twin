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
    themeButton: $("#theme-button"),
    toast: $("#toast"),
  };

  const state = {
    sessionId: null, events: null, candidates: [], active: null,
    drafts: [], busy: false, unread: 0, contact: null,
    company: "", transport: "remote",
    // The answer in flight: the tool events arrive on the session's SSE stream
    // and have to reach the turn that is waiting for them.
    pending: null,
  };

  // The hard per-session token budget is a real limit, so it is stated rather
  // than hidden. Absent from layouts that have no rail to state it in.
  function showBudget(response) {
    const row = $("#rail-budget-row");
    const value = $("#rail-budget");
    if (!row || !value || typeof response.budget_remaining !== "number") return;
    row.hidden = false;
    value.textContent = `${response.budget_remaining.toLocaleString()} tokens`;
  }

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

  let toastTimer;
  function toast(msg) {
    el.toast.textContent = msg;
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.toast.hidden = true), 3800);
  }

  // twin-local.js loads in both deployments, but only the static build lets it
  // answer endpoints: there it is the whole backend, here it is only the engine
  // behind the retrieval explorer.
  const engine = window.__TWIN_LOCAL__ || null;
  const apiBase = String(window.__TWIN_API_BASE__ || "").replace(/\/$/, "");
  const offline = engine && window.__TWIN_OFFLINE__ === true && !apiBase;
  state.transport = offline ? "local" : "remote";

  async function remoteApi(path, options = {}) {
    const res = await fetch(`${apiBase}${path}`, {
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      ...options,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      const error = new Error(detail.detail || `Request failed (${res.status})`);
      error.httpStatus = res.status;
      throw error;
    }
    return res.status === 204 ? null : res.json();
  }

  async function api(path, options = {}) {
    if (state.transport === "local") {
      const local = await engine.handle(path, options);
      if (local !== undefined) return local;
      throw new Error("Live research tools are still connecting. Try again shortly.");
    }
    try {
      return await remoteApi(path, options);
    } catch (error) {
      // A typed API rejection (validation, rate limit, budget) is authoritative;
      // the local fallback must not turn it into an unlimited successful request.
      if (error.httpStatus) throw error;
      // The local corpus is an honest, useful fallback when the hosted API is
      // unreachable. It cannot scan companies, but the core twin keeps working.
      if (engine) {
        const local = await engine.handle(path, options);
        if (local !== undefined) return local;
      }
      throw error;
    }
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
      toast("Thanks, I'll tailor what I show you.");
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

  const secs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);

  /*
    Answers arrive as plain text that frequently contains paragraphs and dashed
    lists. Rendered with white-space: pre-wrap they collapse into one grey slab,
    so the structure the model produced is reconstructed here. The input is
    escaped first: only the tags added below can reach the DOM.
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
          return `<${tag}>${lines
            .map((l) => `<li>${bold(esc(l.replace(/^([-*•]|\d+[.)])\s+/, "")))}</li>`)
            .join("")}</${tag}>`;
        }
        return `<p>${bold(esc(block.trim())).replace(/\n/g, "<br>")}</p>`;
      })
      .join("");
  }

  /* One call in the agent's tool loop, live and then kept under the answer. */
  function stepRow(step) {
    const status = step.status || "running";
    const meta = [
      status === "running" || status === "ok" ? null : status,
      typeof step.duration_ms === "number" ? secs(step.duration_ms) : null,
      step.cached ? "cached" : null,
    ].filter(Boolean).join(" · ");
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

  // The claim status is the point of a grounded twin, so it is stated rather
  // than left to be inferred from the wording. The two refusals are not the
  // same thing: a contractual question is declined on policy and still cites
  // the boundary it was declined under; an unevidenced one lacks proof.
  function badges(meta) {
    const out = [];
    if (meta.refusal) {
      out.push(meta.grounded
        ? '<span class="badge warn">Not the twin\'s to answer</span>'
        : '<span class="badge warn">No evidence for this</span>');
    } else if (meta.grounded) {
      out.push('<span class="badge ok">Grounded in sources</span>');
    }
    if (meta.tailored_for) {
      out.push(`<span class="badge">Tailored for ${esc(meta.tailored_for)}</span>`);
    }
    return out.length ? `<div class="badges">${out.join("")}</div>` : "";
  }

  function turn(role, text, cites, trace, meta = {}) {
    const div = document.createElement("div");
    div.className = `turn ${role}`;
    // "CV › Experience › matriXploit Pvt. Ltd. › Software Engineer" is
    // four levels of breadcrumb for a chip. The last two identify it; the full
    // path stays available on hover for anyone checking the source.
    const chips = (cites || []).map((c) => {
      const parts = String(c).split("›").map((p) => p.trim()).filter(Boolean);
      const short = parts.length > 2 ? parts.slice(-2).join(" › ") : parts.join(" › ");
      return `<span class="cite" title="${esc(c)}">${esc(short)}</span>`;
    }).join("");
    /*
      Two different things arrive as `trace` and both are worth showing. The
      static build retrieves in the browser and reports the shape of that
      retrieval. The server runs a bounded tool loop and reports every call it
      made. Reading `.candidates` off the server's array gave "undefined chunks
      scored" on every answer the server produced.
    */
    const steps = Array.isArray(trace) ? trace : [];
    const spent = steps.reduce((total, s) => total + (s.duration_ms || 0), 0);
    const calls = `${steps.length} tool ${steps.length === 1 ? "call" : "calls"}`;
    const retrieval = steps.length
      ? `<details class="trace-panel">
           <summary>How this was assembled · ${calls} · ${secs(spent)}</summary>
           <ol class="steps">${stepRows(steps)}</ol>
         </details>`
      : (trace && typeof trace === "object" && trace.candidates !== undefined
        ? `<div class="trace">${esc(trace.candidates)} chunks scored · BM25 + trigram ·`
          + ` fused by RRF · ${esc((cites || []).length)} cited</div>`
        : "");
    div.innerHTML = `
      <div class="bubble">
        <span class="label">${role === "twin" ? "Prathamesh" : "You"}</span>
        <div class="text">${role === "twin" ? format(text) : esc(text)}</div>
        ${role === "twin" && text ? badges(meta) : ""}
        ${chips ? `<div class="cites">${chips}</div>` : ""}
        ${retrieval}
        ${role === "twin" && text
          ? `<div class="turn-actions">
               <button type="button" data-copy>Copy</button>
               ${(cites || []).length ? '<button type="button" data-copy-cited>Copy with sources</button>' : ""}
             </div>`
          : ""}
      </div>`;
    el.messages.appendChild(div);
    // On the body, not the hero: the deck and rails need to react too, and CSS
    // cannot reach a parent from the element that changed.
    document.body.classList.add("has-thread");
    // "nearest" only scrolls when the turn is actually off-screen. Aligning to
    // "end" threw the page down past the conversation into the sections below,
    // so the answer landed above the fold and the chat looked like it had done
    // nothing at all.
    div.scrollIntoView({
      behavior: "smooth",
      block: matchMedia("(max-width: 700px)").matches ? "start" : "nearest",
    });
    return div;
  }

  async function ask(text) {
    if (!text.trim() || state.busy || !state.sessionId) return;
    state.busy = true;
    el.send.disabled = true;
    el.composer.setAttribute("aria-busy", "true");
    el.starters.inert = true;
    if (el.intro) el.intro.hidden = true;
    turn("you", text);
    el.input.value = "";
    el.input.style.height = "auto";
    // A grounded answer takes several seconds. Silent dots are indistinguishable
    // from a broken page, so say what is happening and keep a running clock.
    const pending = turn("twin", "");
    const slot = pending.querySelector(".text");
    slot.innerHTML =
      '<span class="waiting"><span class="typing"><i></i><i></i><i></i></span>' +
      '<span class="waiting-label">Finding the strongest evidence…</span>' +
      '<span class="waiting-clock">0s</span></span>' +
      '<ol class="steps live"></ol>';
    const startedAt = Date.now();
    const clock = slot.querySelector(".waiting-clock");
    const label = slot.querySelector(".waiting-label");
    // The tool events arrive on the session stream and have to find the turn
    // that is waiting for them.
    state.pending = { steps: [], host: slot.querySelector(".steps"), label, tooled: false };
    const ticker = setInterval(() => {
      const elapsed = Math.round((Date.now() - startedAt) / 1000);
      clock.textContent = `${elapsed}s`;
      // Once real tool events are arriving they say what is happening; the timed
      // copy is only the fallback for the retrieval-only path.
      if (state.pending?.tooled) return;
      if (elapsed === 3) label.textContent = "Drafting a grounded answer…";
      if (elapsed === 8) label.textContent = "Verifying every claim…";
      if (elapsed === 18) label.textContent = "The live model is taking longer than usual…";
    }, 1000);

    /*
      A chat request had no deadline. When the provider or the tool loop stalls,
      the visitor was left with a spinner that never resolved and a send button
      that stayed disabled, with no way back except reloading the page — which
      reads exactly like a broken chat, because it is one. The server's own tool
      wall-clock is well under this, so anything past it is genuinely stuck.
    */
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 32_000);
    try {
      const r = await api(`/api/sessions/${state.sessionId}/chat`, {
        method: "POST", body: JSON.stringify({ message: text }),
        signal: controller.signal,
      });
      pending.remove();
      turn("twin", r.answer, r.sources, r.trace, {
        grounded: r.grounded, refusal: r.refusal, tailored_for: r.tailored_for,
      });
      showBudget(r);
    } catch (e) {
      pending.remove();
      turn("twin", e.name === "AbortError"
        ? "That took too long and I stopped waiting for it. Ask again, or try a "
          + "narrower question."
        : `Sorry, ${e.message}`);
    } finally {
      clearTimeout(deadline);
      clearInterval(ticker);
      state.pending = null;
      state.busy = false;
      el.send.disabled = false;
      el.composer.removeAttribute("aria-busy");
      el.starters.inert = false;
      // preventScroll matters: refocusing the composer otherwise drags the
      // viewport down to it, past the answer that just arrived.
      el.input.focus({ preventScroll: true });
    }
  }

  el.composer.addEventListener("submit", (e) => { e.preventDefault(); ask(el.input.value); });
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(el.input.value); }
  });
  el.input.addEventListener("input", () => {
    el.input.style.height = "auto";
    el.input.style.height = `${Math.min(el.input.scrollHeight, 170)}px`;
  });

  el.starters.innerHTML = STARTERS.map((s) => `<button type="button">${esc(s)}</button>`).join("");
  el.starters.addEventListener("click", (e) => {
    if (e.target.tagName === "BUTTON") ask(e.target.textContent);
  });

  /*
    Anything carrying data-ask puts its own question to the twin, wherever it
    sits on the page. The stack tiles read as buttons and did nothing; now the
    whole rail is a way into the conversation rather than a list of words.
  */
  /*
    In-page links land where the section was when the scroll started, and this
    page keeps growing underneath one: repository cards arrive lazily, reveals
    change heights as they fire, and a smooth scroll takes long enough for all
    of it. The Work link in particular missed its own section every time. So the
    jump is made, and then made again once the page has stopped moving.
  */
  document.addEventListener("click", (e) => {
    const link = e.target.closest('a[href^="#"]:not([href="#"])');
    if (link) {
      const target = document.querySelector(link.getAttribute("href"));
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        // Two corrections: one after the smooth scroll should have finished,
        // one after anything it triggered has settled.
        setTimeout(() => target.scrollIntoView({ behavior: "auto", block: "start" }), 620);
        setTimeout(() => target.scrollIntoView({ behavior: "auto", block: "start" }), 1100);
      }
      return;
    }
    const trigger = e.target.closest("[data-ask]");
    if (!trigger) return;
    e.preventDefault();
    const question = trigger.dataset.ask;
    if (!question) return;
    // The composer is the focal artefact: bring it into view so the answer is
    // not delivered somewhere off-screen.
    el.input.scrollIntoView({ behavior: "smooth", block: "center" });
    ask(question);
  });

  /* ---------- events ---------- */

  function openEvents() {
    const src = new EventSource(`${apiBase}/api/sessions/${state.sessionId}/events`);
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
      them live is what makes the execution contract visible: which public
      sources were touched, in order, while the visitor waits — and the same
      list stays attached to the finished answer.
    */
    src.addEventListener("tool.call", (e) => {
      const p = JSON.parse(e.data);
      const live = state.pending;
      if (!live) return;
      live.tooled = true;
      // The step list spells out each call, so the headline stays a headline
      // rather than repeating the last row verbatim.
      live.label.textContent = "Consulting public sources…";
      live.steps.push({ ...p, status: "running" });
      live.host.innerHTML = stepRows(live.steps);
      // Activity logged only research and failures, so a session where every
      // tool succeeded left the panel empty. The calls are the activity.
      note(p.phrase || `Called ${p.tool}`);
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

    src.addEventListener("roles.ready", (e) => {
      const p = JSON.parse(e.data);
      const result = Object.values(p.roles || {}).find((row) => row?.roles?.length);
      if (!result) return;
      showOpportunities({
        company: state.company || "this company",
        status: result.status,
        summary: result.reason,
        roles: result.roles,
        public_sources_only: true,
        automatic_application: false,
      });
      note(`Ranked ${result.roles.length} public role(s)`);
    });

    src.addEventListener("outreach.action", (e) => {
      const p = JSON.parse(e.data);
      if (p.status === "sent") note("Email sent");
      else if (p.reason) note(`Email not sent: ${p.reason}`);
    });

    src.onerror = () => { /* EventSource retries; chat must keep working. */ };
  }

  /* ---------- onboarding ---------- */

  function dismissOnboarding() {
    el.onboarding.hidden = true;
    // Moving focus is required because hidden controls otherwise keep keyboard
    // focus, making the first post-onboarding Tab jump unpredictable.
    el.input.focus({ preventScroll: true });
  }

  el.identityForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = el.visitorName.value.trim();
    const company = el.visitorCompany.value.trim();
    state.company = company;
    dismissOnboarding();
    const identity = name
      ? api(`/api/sessions/${state.sessionId}/identity`, {
        method: "POST", body: JSON.stringify({ name, company: company || null }),
      })
      : api(`/api/sessions/${state.sessionId}/skip`, { method: "POST" });
    const radar = company ? scanCompanyRoles(company) : Promise.resolve();
    const settled = await Promise.allSettled([identity, radar]);
    const failed = settled.find((item) => item.status === "rejected");
    if (failed) toast(failed.reason?.message || "One optional lookup was unavailable.");
  });

  el.skipButton.addEventListener("click", async () => {
    dismissOnboarding();
    try { await api(`/api/sessions/${state.sessionId}/skip`, { method: "POST" }); } catch {}
  });

  /* ---------- drawer ---------- */

  let drawerReturnFocus = null;

  function openDrawer(title, html) {
    if (el.drawer.hidden) drawerReturnFocus = document.activeElement;
    el.drawerTitle.textContent = title;
    el.drawerBody.innerHTML = html;
    el.drawer.hidden = false;
    requestAnimationFrame(() => el.drawerClose.focus({ preventScroll: true }));
  }
  function closeDrawer() {
    if (el.drawer.hidden) return;
    el.drawer.hidden = true;
    if (drawerReturnFocus?.isConnected) {
      drawerReturnFocus.focus({ preventScroll: true });
    }
    drawerReturnFocus = null;
  }

  function roleMarkup(role) {
    const evidence = (role.evidence || []).slice(0, 2);
    const place = [role.team, role.location].filter(Boolean).join(" · ");
    return `
      <article class="opportunity">
        <div class="opportunity-head">
          <div><h3>${esc(role.title)}</h3>${place ? `<p>${esc(place)}</p>` : ""}</div>
          <strong aria-label="Evidence fit ${esc(role.fit_score)} percent">${esc(role.fit_score)}%</strong>
        </div>
        ${evidence.length ? `<ul>${evidence.map((item) =>
          `<li><span>${esc(item.signal)}</span>${esc(item.evidence)}</li>`).join("")}</ul>` : ""}
        <div class="opportunity-foot">
          <span>${esc(String(role.ats || "careers page").replaceAll("_", " "))}</span>
          <a class="btn" href="${esc(role.canonical_apply_url)}" target="_blank" rel="noopener noreferrer">View original role ↗</a>
        </div>
      </article>`;
  }

  function showOpportunities(result) {
    const roles = result.roles || [];
    const company = result.company || state.company || "the company";
    if (!roles.length) {
      openDrawer("Opportunity radar", `
        <p class="over-lede">${esc(result.summary || `No attributable public engineering roles were found at ${company}.`)}</p>
        <p class="radar-note">Public sources only. No application was made.</p>`);
      return;
    }
    openDrawer("Opportunity radar", `
      <p class="over-lede">${esc(company)} · ${roles.length} public role${roles.length === 1 ? "" : "s"}, ranked against Prathamesh's verified CV evidence.</p>
      <div class="opportunity-list">${roles.map(roleMarkup).join("")}</div>
      <p class="radar-note">Fit scores are evidence coverage, not a hiring prediction. Public sources only; no application was made.</p>`);
  }

  async function scanCompanyRoles(company) {
    openDrawer("Opportunity radar", `
      <div class="radar-loading" role="status"><i></i><div><strong>Scanning ${esc(company)}</strong><p>Finding the public careers page, detecting its ATS and ranking relevant roles…</p></div></div>`);
    const result = await api(`/api/sessions/${state.sessionId}/opportunities`, {
      method: "POST", body: JSON.stringify({ company }),
    });
    showOpportunities(result);
    return result;
  }
  el.drawerClose.addEventListener("click", closeDrawer);
  el.drawer.addEventListener("click", (e) => {
    if (e.target === el.drawer || e.target.dataset.close !== undefined) closeDrawer();
  });

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
    (requirement/evidence/source), not_evidenced, summary and caveat. The gap
    list was read from `fit.unevidenced`, a name the API has never returned, so
    the honest half of the analysis — the requirements the CV does not evidence —
    rendered as nothing on every description ever pasted in.
  */
  function renderFit(fit) {
    const matched = fit.matched || [];
    const gaps = fit.not_evidenced || [];
    const pct = Math.max(0, Math.min(100, Number(fit.coverage_percent) || 0));
    return `
      <div class="fit-head">
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
      ${fit.caveat ? `<p class="fit-caveat">${esc(fit.caveat)}</p>` : ""}`;
  }

  el.jdButton.addEventListener("click", () => {
    openDrawer("Role fit", `
      <p class="over-lede">Paste a job description. Requirements are split into
        directly evidenced and not evidenced, never quietly upgraded.</p>
      <form class="jd-form" id="jd-form">
        <textarea id="jd-input" placeholder="Paste the job description…"></textarea>
        <div class="sheet-actions"><button type="submit" class="btn">Check fit</button></div>
      </form><div id="jd-results"></div>`);
    $("#jd-input")?.focus();
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
        // style-src is 'self', so the coverage ring cannot carry an inline style
        // attribute. CSSOM is not inline style, and is allowed.
        const meter = $(".fit-meter", out);
        meter?.style.setProperty("--pct", meter.dataset.pct);
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
    closeDrawer();
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
  function renderWorkCards(repos) {
    const host = $("#work-cards");
    if (!host) return;
    // The reserved height was only there to stop the page moving while the
    // cards were in flight; the cards themselves size the section now.
    host.style.minHeight = "";
    if (!repos.length) { host.closest(".band")?.remove(); return; }
    host.innerHTML = repos.map((r) => `
      <article class="card">
        <h3><a href="${esc(r.url || r.html_url)}" target="_blank" rel="noopener noreferrer">${esc(r.name)}</a></h3>
        <p>${esc(r.description || "")}</p>
        ${r.topics?.length
          ? `<div class="topics">${r.topics.slice(0, 5)
              .map((t) => `<span>${esc(t)}</span>`).join("")}</div>`
          : ""}
      </article>`).join("");
  }

  // Ten repositories cost twenty upstream lookups. When the server had none
  // cached, hold the request until the section is within a screen of the
  // viewport rather than spending it before the visitor has read the hero.
  function armWorkCards() {
    const host = $("#work-cards");
    if (!host) return;
    /*
      The cards arrive after the section is already being scrolled to, and the
      section grows by their full height as they land. A smooth scroll started
      before that lands somewhere else entirely — which is why the Work link in
      the header missed its own section. Holding the space the cards will occupy
      means the page cannot move under the scroll.
    */
    host.style.minHeight = `${Math.ceil(10 / 3) * 214}px`;
    let started = false;
    const load = async () => {
      if (started) return;
      started = true;
      try {
        const data = await api("/api/github");
        renderWorkCards(data.repositories || data.repos || []);
      } catch { host.closest(".band")?.remove(); }
    };
    if (!("IntersectionObserver" in window)) { load(); return; }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { io.disconnect(); load(); }
    }, { rootMargin: "600px 0px" });
    io.observe(host);
  }

  /* ---------- retrieval explorer ---------- */

  const rankList = (title, rows, note) => `
    <div class="rank">
      <span class="rank-head">${esc(title)}</span>
      ${note ? `<span class="rank-note">${esc(note)}</span>` : ""}
      <ol>${rows.map((row) => `
        <li><span class="rank-src">${esc(row.label)}</span>
        <span class="rank-score">${row.score.toFixed(3)}</span></li>`).join("")}</ol>
    </div>`;

  function renderRetrieval(host, result) {
    if (!result) {
      host.innerHTML = '<p class="retrieval-empty">Every term in that query is a '
        + 'stop word, so there is nothing to score. Try a noun.</p>';
      return;
    }
    // The fused column is the interesting one: showing where each result placed
    // in the two input rankings is what makes the fusion legible rather than
    // magic.
    const fused = result.fused.map((row) => {
      const from = [
        row.lexicalRank ? `BM25 #${row.lexicalRank}` : null,
        row.denseRank ? `trigram #${row.denseRank}` : null,
      ].filter(Boolean).join(" · ");
      return `
        <li>
          <span class="rank-src">${esc(row.label)}</span>
          <span class="rank-score">${row.score.toFixed(4)}</span>
          <span class="rank-from">${esc(from || "unranked in both")}</span>
          <p class="rank-text">${esc(row.text.slice(0, 190))}${row.text.length > 190 ? "…" : ""}</p>
        </li>`;
    }).join("");

    host.innerHTML = `
      <p class="retrieval-meta">${esc(result.scanned)} chunks scanned ·
        ${esc(result.terms.length)} query terms after expansion ·
        ${esc(result.lexical.length)} matched lexically ·
        ${esc(result.dense.length)} matched by trigram</p>
      <div class="rank-row">
        ${rankList("BM25", result.lexical, "term weighting")}
        ${rankList("Trigram cosine", result.dense, "surface similarity")}
      </div>
      <div class="rank fused">
        <span class="rank-head">Fused (RRF, k=60)</span>
        <span class="rank-note">what the answer is built from</span>
        <ol>${fused}</ol>
      </div>`;
  }

  function armRetrieval() {
    const form = $("#retrieval-form");
    const input = $("#retrieval-input");
    const out = $("#retrieval-out");
    if (!form || !engine) {
      // Without the engine there is nothing honest to show, so the section goes
      // rather than sitting there broken.
      $("#ai-systems")?.remove();
      return;
    }
    const run = async () => {
      out.innerHTML = `<p class="retrieval-empty">Scoring…</p>`;
      try {
        const [{ corpus }, result] = await Promise.all([
          engine.load(),
          engine.explore(input.value.trim()),
        ]);
        renderRetrieval(out, result);
        renderMcp(corpus.mcp);
        renderAgents(corpus.repositories);
      } catch {
        $("#ai-systems")?.remove();
      }
    };
    form.addEventListener("submit", (e) => { e.preventDefault(); run(); });
    // First paint is deferred until the section is approached: building the
    // index costs a corpus fetch nobody above the fold asked for.
    if (!("IntersectionObserver" in window)) { run(); return; }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { io.disconnect(); run(); }
    }, { rootMargin: "300px 0px" });
    io.observe(form);
  }

  /* ---------- MCP servers ---------- */

  const QUALITY_METRICS = ["recall_at_k", "precision_at_k", "mrr", "ndcg_at_k", "map"];

  // The agent systems among the ten allow-listed repositories. Everything shown
  // about them -- description, language, topics, last commit -- comes from the
  // live GitHub payload, so this panel cannot describe a repository as something
  // it is not.
  const AGENT_REPOS = [
    "agent-runtime", "agent-mesh", "agent-redteam", "effect-broker",
    "effect-browser", "answer-engine", "llm-gateway",
  ];

  function renderAgents(repos) {
    const panel = $("#agents-panel");
    const grid = $("#agents-grid");
    if (!panel || !grid || !Array.isArray(repos)) return;
    const byName = new Map(repos.map((repo) => [repo.name, repo]));
    const chosen = AGENT_REPOS.map((name) => byName.get(name)).filter(Boolean);
    if (!chosen.length) return;

    $("#agents-lede").textContent =
      `${chosen.length} of the ten public systems are agent infrastructure: durable `
      + `execution, effect authorisation, adversarial testing, multi-agent transport, `
      + `retrieval and model routing. Descriptions and activity below are read live `
      + `from GitHub, not written here.`;

    grid.innerHTML = chosen.map((repo) => {
      const commit = repo.commits?.[0] || repo.last_commit;
      const when = commit?.committed_at || repo.updated_at;
      const stamp = when
        ? new Date(when).toLocaleDateString([], { month: "short", year: "numeric" })
        : "";
      return `
        <article class="mcp-card">
          <h4><a href="${esc(repo.url)}" target="_blank" rel="noopener noreferrer">${esc(repo.name)}</a></h4>
          <p>${esc(repo.description || "")}</p>
          <div class="mcp-counts">
            ${repo.language ? `<span><b>${esc(repo.language)}</b>language</span>` : ""}
            ${stamp ? `<span><b>${esc(stamp)}</b>last commit</span>` : ""}
          </div>
          ${repo.topics?.length ? `<div class="topics">${repo.topics.slice(0, 5)
            .map((topic) => `<span>${esc(topic)}</span>`).join("")}</div>` : ""}
        </article>`;
    }).join("");
    panel.hidden = false;
  }

  // Rendered from the manifest the mcp-servers repo publishes, so this panel
  // cannot claim a server that was not built. Absent manifest, absent panel.
  function renderMcp(manifest) {
    const panel = $("#mcp-panel");
    const grid = $("#mcp-grid");
    if (!panel || !grid) return;
    const servers = Array.isArray(manifest) ? manifest : manifest?.servers;
    if (!Array.isArray(servers) || !servers.length) return;

    const count = (value) => (Array.isArray(value) ? value.length : 0);
    const repo = manifest?.repository;
    const detail = [
      manifest?.transport ? `${manifest.transport} transport` : null,
      manifest?.sdk ? `SDK ${manifest.sdk}` : null,
    ].filter(Boolean).join(" · ");
    $("#mcp-lede").innerHTML =
      `${esc(servers.length)} servers speaking the Model Context Protocol. Each exposes `
      + `tools, resources and prompts with structured output schemas, and none letting a `
      + `low-level exception cross the protocol boundary.`
      + (detail ? ` <span class="mcp-detail">${esc(detail)}</span>` : "")
      + (repo ? ` <a href="${esc(repo)}" target="_blank" rel="noopener noreferrer">Source ↗</a>` : "");

    grid.innerHTML = servers.map((server) => {
      const counts = [
        [count(server.tools), "tools"],
        [count(server.resources), "resources"],
        [count(server.prompts), "prompts"],
      ].filter(([n]) => n > 0);
      // Every tool, resource and prompt by name. The counts alone read as a
      // claim; the names are the thing a reader can go and check.
      const chips = (items, key) => (items || [])
        .map((item) => esc(typeof item === "string" ? item : item[key] || ""))
        .filter(Boolean);
      const listing = [
        ["Tools", chips(server.tools, "name")],
        ["Resources", chips(server.resources, "uri")],
        ["Prompts", chips(server.prompts, "name")],
      ].filter(([, values]) => values.length)
        .map(([label, values]) => `
          <div class="mcp-list"><span class="mcp-list-label">${label}</span>
          <div class="topics">${values
            .map((value) => `<span>${value}</span>`).join("")}</div></div>`)
        .join("");
      // Measured retrieval quality is the whole reason to trust the RAG claim.
      // Only quality metrics get a tile, the query count and k describe the
      // experiment, and showing them as scores makes the numbers unreadable.
      const scored = server.eval && typeof server.eval === "object" ? server.eval : null;
      const at = scored?.k ?? "k";
      const scores = QUALITY_METRICS
        .filter((key) => typeof scored?.[key] === "number")
        .map((key) => `<span><b>${scored[key].toFixed(3)}</b>${esc(
          key.replace(/_at_k$/, `@${at}`).replace(/_/g, " "))}</span>`)
        .join("");
      const basis = scores && scored.query_count
        ? `<p class="mcp-basis">measured over ${esc(scored.query_count)} fixture queries</p>`
        : "";
      const install = server.install_command || server.install;
      return `
        <article class="mcp-card">
          <h4>${esc(server.name || "server")}</h4>
          <p>${esc(server.description || "")}</p>
          ${counts.length ? `<div class="mcp-counts">${counts
            .map(([n, label]) => `<span><b>${esc(n)}</b>${esc(label)}</span>`)
            .join("")}</div>` : ""}
          ${scores ? `<div class="mcp-counts mcp-eval">${scores}</div>${basis}` : ""}
          ${listing}
          ${install ? `<code class="mcp-install">${esc(install)}</code>` : ""}
        </article>`;
    }).join("");
    panel.hidden = false;
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
    // Light glass is right over the sky at the top of the page and wrong
    // everywhere else: at that fill, body copy and cards passing under the bar
    // stayed legible through it and collided with the nav labels. Past the
    // first scroll it takes the heavy fill.
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
    // Pasting an answer into an ATS without its sources strips exactly the part
    // that makes it checkable, so citations travel with it on request.
    const cited = [...(bubble?.querySelectorAll(".cite") || [])]
      .map((n) => `- ${n.getAttribute("title") || n.textContent}`).join("\n");
    const withSources = button.hasAttribute("data-copy-cited");
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
    // The stylesheet leaves content visible by default; this class is what hides
    // it for the animation. Setting it here, in the same breath as the observer
    // that reveals it, means no failure path can leave the page blank — which is
    // what an opacity:0 default did whenever this file stopped early.
    document.documentElement.classList.add("reveals");
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

  const BOOT_SOFT_DEADLINE_MS = 2400;

  const wait = (milliseconds) => new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });

  async function bootstrapSession() {
    if (offline || !apiBase || !engine) {
      return {
        start: await api("/api/bootstrap", { method: "POST", body: "{}" }),
        mode: state.transport,
      };
    }

    // A sleeping free service must not turn the entire first screen into a
    // spinner. After a short grace period, boot the evidence-only local twin
    // while the live model continues warming in the background.
    const remote = remoteApi("/api/bootstrap", { method: "POST", body: "{}" });
    const first = await Promise.race([
      remote.then(
        (start) => ({ kind: "remote", start }),
        (error) => ({ kind: "error", error }),
      ),
      wait(BOOT_SOFT_DEADLINE_MS).then(() => ({ kind: "slow" })),
    ]);
    if (first.kind === "remote") {
      state.transport = "remote";
      return { start: first.start, mode: "remote" };
    }

    const start = await engine.handle(
      "/api/bootstrap", { method: "POST", body: "{}" },
    );
    state.transport = "local";
    return {
      start,
      mode: "local",
      deferredRemote: first.kind === "slow" ? remote : null,
    };
  }

  function showModel(start) {
    const model = start.health?.model;
    if (!model) return;
    el.modelNote.textContent = model;
    const railModel = $("#rail-model");
    if (railModel) railModel.textContent = model;
  }

  (async function boot() {
    // Nothing here needs the network, so it must not wait behind it.
    stagger(".timeline .reveal", 80);
    armReveals();
    armRetrieval();

    let start;
    let boot;
    try {
      boot = await bootstrapSession();
      start = boot.start;
    } catch (e) {
      turn("twin", `Couldn't start a session: ${e.message}`);
      el.contactLink?.remove();
      return;
    }

    showModel(start);

    state.contact = start.contact || null;
    // Optional: contact now lives in the slide-over, so the inline link may not
    // be present in this layout.
    if (state.contact?.email && el.contactLink) {
      el.contactLink.href = `mailto:${state.contact.email}`;
    } else {
      el.contactLink?.remove();
    }

    state.sessionId = start.session.session_id;
    // No greeting turn: the opening line above the input already says what the
    // twin is and what it can be asked, and repeating it reads as a bug.
    if (boot.mode === "remote") {
      openEvents();
      el.onboarding.hidden = false;
      el.visitorName.focus();
    } else {
      // Visitor research needs a server and a search provider. Asking for a
      // name the static build cannot act on would be theatre.
      el.onboarding?.remove();
      el.input.focus({ preventScroll: true });
    }

    // Rendered straight from the bootstrap payload when the server already had
    // GitHub warm; otherwise held until the section is nearly in view.
    if (start.repositories) renderWorkCards(start.repositories);
    else armWorkCards();

    if (boot.deferredRemote) {
      boot.deferredRemote.then((remoteStart) => {
        if (!remoteStart?.session?.session_id) return;
        state.transport = "remote";
        state.sessionId = remoteStart.session.session_id;
        state.contact = remoteStart.contact || state.contact;
        showModel(remoteStart);
        openEvents();
        toast("Live AI connected.");
      }).catch(() => {
        // The local evidence engine remains a complete grounded chat fallback.
      });
    }
  })();
})();
