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
  };

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

  // The published static build has no server behind it. twin-local.js answers
  // the same endpoints from a compiled corpus and returns undefined for the ones
  // that genuinely need a database, so one code path serves both deployments.
  const offline = window.__TWIN_LOCAL__ || null;

  async function api(path, options = {}) {
    if (offline) {
      const local = await offline.handle(path, options);
      if (local !== undefined) return local;
    }
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

  function turn(role, text, cites, trace) {
    const div = document.createElement("div");
    div.className = `turn ${role}`;
    const chips = (cites || []).map((c) => `<span class="cite">${esc(c)}</span>`).join("");
    // Showing the retrieval shape is the point, not decoration: it is what
    // separates a grounded answer from a confident-sounding one.
    const retrieval = trace
      ? `<div class="trace">${esc(trace.candidates)} chunks scored · BM25 + trigram ·`
        + ` fused by RRF · ${esc((cites || []).length)} cited</div>`
      : "";
    div.innerHTML = `
      <div class="bubble">
        <span class="label">${role === "twin" ? "Prathamesh" : "You"}</span>
        <div class="text">${esc(text)}</div>
        ${chips ? `<div class="cites">${chips}</div>` : ""}
        ${retrieval}
        ${role === "twin" && text
          ? `<div class="turn-actions"><button type="button" data-copy>Copy</button></div>`
          : ""}
      </div>`;
    el.messages.appendChild(div);
    // "nearest" only scrolls when the turn is actually off-screen. Aligning to
    // "end" threw the page down past the conversation into the sections below,
    // so the answer landed above the fold and the chat looked like it had done
    // nothing at all.
    div.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return div;
  }

  async function ask(text) {
    if (!text.trim() || state.busy || !state.sessionId) return;
    state.busy = true;
    el.send.disabled = true;
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
      '<span class="waiting-label">Searching his CV and repositories…</span>' +
      '<span class="waiting-clock">0s</span></span>';
    const startedAt = Date.now();
    const clock = slot.querySelector(".waiting-clock");
    const label = slot.querySelector(".waiting-label");
    const ticker = setInterval(() => {
      const secs = Math.round((Date.now() - startedAt) / 1000);
      clock.textContent = `${secs}s`;
      if (secs === 4) label.textContent = "Drafting a grounded answer…";
      if (secs === 12) label.textContent = "Still working — verifying every claim…";
    }, 1000);

    try {
      const r = await api(`/api/sessions/${state.sessionId}/chat`, {
        method: "POST", body: JSON.stringify({ message: text }),
      });
      clearInterval(ticker);
      pending.remove();
      turn("twin", r.answer, r.sources, r.trace);
    } catch (e) {
      clearInterval(ticker);
      pending.remove();
      turn("twin", `Sorry — ${e.message}`);
    } finally {
      state.busy = false;
      el.send.disabled = false;
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

  el.jdButton.addEventListener("click", () => {
    openDrawer("Role fit", `
      <form class="jd-form" id="jd-form">
        <textarea id="jd-input" placeholder="Paste the job description…"></textarea>
        <div class="sheet-actions"><button type="submit" class="btn">Check fit</button></div>
      </form><div id="jd-results"></div>`);
    $("#jd-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const description = $("#jd-input").value.trim();
      if (!description) return;
      const out = $("#jd-results");
      out.innerHTML = "<p>Checking…</p>";
      try {
        const fit = await api(`/api/sessions/${state.sessionId}/jd-fit`, {
          method: "POST", body: JSON.stringify({ description }),
        });
        out.innerHTML = `<div class="repo"><strong>Summary</strong><p>${esc(fit.summary || "")}</p></div>` +
          (fit.matched || fit.matched_requirements || []).map((m) =>
            `<div class="repo"><strong>✓ ${esc(m.requirement || m)}</strong><p>${esc(m.source || "")}</p></div>`).join("") +
          (fit.unevidenced || fit.unevidenced_requirements || []).map((m) =>
            `<div class="repo"><strong>— ${esc(m.requirement || m)}</strong><p>Not in the CV.</p></div>`).join("");
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

  /* ---------- scroll progress ---------- */

  const progress = $("#progress");
  if (progress) {
    const paint = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const pct = max > 0 ? (window.scrollY / max) * 100 : 0;
      progress.style.width = `${pct}%`;
    };
    addEventListener("scroll", paint, { passive: true });
    addEventListener("resize", paint);
    paint();
  }

  /* ---------- copy an answer ---------- */

  // Recruiters paste answers into their notes or ATS; making that one click
  // keeps the twin's exact wording intact rather than a lossy manual selection.
  el.messages.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    const text = button.closest(".turn")?.querySelector(".text")?.innerText || "";
    try {
      await navigator.clipboard.writeText(text.trim());
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = "Copy"), 1800);
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
    // Nothing here needs the network, so it must not wait behind it.
    stagger(".timeline .reveal", 80);
    armReveals();

    let start;
    try {
      start = await api("/api/bootstrap", { method: "POST", body: "{}" });
    } catch (e) {
      turn("twin", `Couldn't start a session: ${e.message}`);
      el.contactLink?.remove();
      return;
    }

    const model = start.health?.model;
    if (model) {
      el.modelNote.textContent = model;
      const railModel = $("#rail-model");
      if (railModel) railModel.textContent = model;
    }

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
    if (!offline) {
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
  })();
})();
