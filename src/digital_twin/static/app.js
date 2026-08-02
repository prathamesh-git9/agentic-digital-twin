/*
  Digital Twin — frontend controller.

  The rail is a single state machine driven by the `research` SSE event, because
  the visitor should never have to guess what the system knows. Research payloads
  are rendered as inert card data only; authority is granted server-side by
  POST /confirm, so nothing here can leak candidate context into the model.
*/
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);

  const el = {
    page: $("#page"),
    messages: $("#messages"),
    starters: $("#starters"),
    composer: $("#composer"),
    input: $("#composer-input"),
    send: $("#send-button"),
    budget: $("#budget-pill"),
    providerLabel: $("#provider-label"),

    strip: $("#research-strip"),
    stripTitle: $("#strip-title"),
    stripDetail: $("#strip-detail"),
    stripView: $("#strip-view"),
    stripOptout: $("#strip-optout"),

    gate: $("#gate"),
    gateStage: $("#gate-stage"),
    sourceList: $("#source-list"),
    sourceProgress: $("#source-progress"),

    candidateList: $("#candidate-list"),
    candidateCount: $("#candidate-count"),
    candidatesOptout: $("#candidates-optout"),

    activeConfidence: $("#active-confidence"),
    activeAvatar: $("#active-avatar"),
    activeInitials: $("#active-initials"),
    activeName: $("#active-name"),
    activeRole: $("#active-role"),
    activeLocation: $("#active-location"),
    activeProfiles: $("#active-profiles"),
    emailRow: $("#active-email-row"),
    emailLink: $("#active-email"),
    emailStatus: $("#active-email-status"),
    companyBlock: $("#company-block"),
    companyBody: $("#company-body"),
    rolesBlock: $("#roles-block"),
    rolesBody: $("#roles-body"),
    rolesCount: $("#roles-count"),
    activeOptout: $("#active-optout"),

    outreachBlock: $("#outreach-block"),
    outreachDecision: $("#outreach-decision"),
    variantTabs: $("#variant-tabs"),
    draftBody: $("#draft-body"),
    sendEmail: $("#send-email"),
    openLinkedin: $("#open-linkedin"),
    outreachNote: $("#outreach-note"),

    evidenceList: $("#evidence-list"),
    sourceCount: $("#source-count"),

    onboarding: $("#onboarding"),
    identityForm: $("#identity-form"),
    visitorName: $("#visitor-name"),
    visitorCompany: $("#visitor-company"),
    skipButton: $("#skip-button"),

    drawer: $("#drawer"),
    drawerTitle: $("#drawer-title"),
    drawerBody: $("#drawer-body"),
    drawerClose: $("#drawer-close"),
    projectsButton: $("#projects-button"),
    jdButton: $("#jd-button"),

    themeButton: $("#theme-button"),
    toast: $("#toast"),
  };

  const state = {
    sessionId: null,
    events: null,
    candidates: [],
    sources: new Map(),
    active: null,
    drafts: [],
    draftIndex: 0,
    variantIndex: 0,
    roles: {},
    busy: false,
  };

  const STARTERS = [
    "Give me the 60-second overview.",
    "What's the hardest problem he's solved?",
    "How does he handle crash recovery?",
    "Is he a fit for a platform team?",
  ];

  /* ---------- helpers ---------- */

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
    );

  // Attributed fields arrive as {value, source_url, ...} or as a bare scalar.
  const val = (field) =>
    field && typeof field === "object" && "value" in field ? field.value : field;

  const srcOf = (field) =>
    field && typeof field === "object" ? field.source_url || null : null;

  const initialsOf = (name) =>
    String(name || "?")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0].toUpperCase())
      .join("");

  let toastTimer;
  function toast(message, tone) {
    el.toast.textContent = message;
    if (tone) el.toast.dataset.tone = tone;
    else delete el.toast.dataset.tone;
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.toast.hidden = true), 4200);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      ...options,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `Request failed (${response.status})`);
    }
    return response.status === 204 ? null : response.json();
  }

  /* ---------- rail state machine ---------- */

  const GATE_BY_STATE = {
    idle: null,
    skipped: null,
    researching: "discovery",
    candidates: "discovery",
    empty: "discovery",
    confirmed: "context",
    opted_out: null,
  };

  function setRailState(name) {
    document.querySelectorAll(".rail-state").forEach((section) => {
      section.hidden = section.dataset.state !== name;
    });
  }

  function renderResearchState(payload) {
    const status = payload?.status || "idle";
    el.page.dataset.research = status;

    // Gate visualisation: highlight how far authority has actually travelled.
    const reached = GATE_BY_STATE[status];
    const order = ["discovery", "confirm", "context"];
    const reachedIndex = reached ? order.indexOf(reached) : -1;
    el.gate.querySelectorAll(".gate-step").forEach((step, index) => {
      step.dataset.active = String(index <= reachedIndex);
    });
    el.gateStage.textContent =
      status === "confirmed" ? "Unlocked" : reachedIndex >= 0 ? "Proposed" : "Locked";

    if (status === "researching") {
      el.strip.hidden = false;
      el.stripTitle.textContent = payload.name
        ? `Checking public sources for ${payload.name}`
        : "Checking public sources";
      el.stripDetail.textContent =
        payload.disclosure || "Search engines only. No LinkedIn login, no data brokers.";
      setRailState("researching");
      return;
    }

    if (status === "candidates") {
      state.candidates = payload.candidates || [];
      el.strip.hidden = false;
      el.stripTitle.textContent = `${state.candidates.length} possible ${
        state.candidates.length === 1 ? "match" : "matches"
      }`;
      el.stripDetail.textContent = "Tell me which one is you and I'll tailor what I show.";
      renderCandidates();
      setRailState("candidates");
      return;
    }

    if (status === "confirmed") {
      el.strip.hidden = true;
      setRailState("active");
      return;
    }

    if (status === "empty") {
      el.strip.hidden = false;
      el.stripTitle.textContent = "Nothing found publicly";
      el.stripDetail.textContent = "That's fine — the conversation is unaffected.";
      setRailState("idle");
      return;
    }

    el.strip.hidden = true;
    setRailState("idle");
  }

  function renderSources() {
    const items = [...state.sources.values()];
    const done = items.filter((item) => item.status !== "running").length;
    el.sourceProgress.textContent = items.length ? `${done}/${items.length}` : "";
    el.sourceList.innerHTML = items
      .map(
        (item) => `
        <li class="source-item" data-status="${esc(item.status)}">
          <span class="name">${esc(item.source)}</span>
          <span class="state">${esc(item.status)}</span>
        </li>`,
      )
      .join("");
  }

  /* ---------- candidates ---------- */

  function renderCandidates() {
    el.candidateCount.textContent = String(state.candidates.length);
    el.candidateList.innerHTML = state.candidates
      .map((candidate, index) => {
        const avatar = candidate.avatar || {};
        const photo = avatar.url || candidate.photo_url;
        const initials = avatar.initials || candidate.initials || initialsOf(candidate.name);
        const why = Array.isArray(candidate.why) ? candidate.why.join(" · ") : candidate.why;
        return `
          <article class="candidate">
            <div class="cand-top">
              ${
                photo
                  ? `<img class="person-avatar" src="${esc(photo)}" alt="" loading="lazy"
                       onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'person-avatar initials',textContent:'${esc(
                         initials,
                       )}'}))">`
                  : `<span class="person-avatar initials">${esc(initials)}</span>`
              }
              <div class="cand-id">
                <strong>${esc(candidate.name)}</strong>
                <span>${esc(candidate.headline || candidate.company || "")}</span>
              </div>
              <span class="score">${esc(candidate.confidence ?? "?")}%</span>
            </div>
            ${why ? `<p class="why">Why ${esc(candidate.confidence)}%: ${esc(why)}</p>` : ""}
            <div class="cand-foot">
              <span class="origin">${esc(candidate.source_label || "public result")}</span>
              <button type="button" class="primary-btn" data-confirm="${index}">This is me</button>
            </div>
          </article>`;
      })
      .join("");
  }

  el.candidateList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-confirm]");
    if (!button) return;
    confirmCandidate(state.candidates[Number(button.dataset.confirm)]);
  });

  async function confirmCandidate(candidate) {
    if (!candidate || !state.sessionId) return;
    try {
      const result = await api(`/api/sessions/${state.sessionId}/confirm`, {
        method: "POST",
        body: JSON.stringify({ candidate_id: candidate.id }),
      });
      applyActive(result);
      renderResearchState({ status: "confirmed" });
      toast("Thanks — I'll tailor what I show you.");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  /* ---------- active person ---------- */

  function applyActive(payload) {
    const candidate = payload.candidate || payload;
    state.active = candidate;

    el.activeConfidence.textContent = candidate.confidence ? `${candidate.confidence}%` : "";
    el.activeName.textContent = candidate.name || "";
    el.activeRole.textContent =
      val(candidate.role) || candidate.headline || val(candidate.company_detail) || candidate.company || "";
    el.activeLocation.textContent = val(candidate.location) || "";

    const avatar = candidate.avatar || {};
    const photo = avatar.url || candidate.photo_url;
    const initials = avatar.initials || candidate.initials || initialsOf(candidate.name);
    if (photo) {
      el.activeAvatar.src = photo;
      el.activeAvatar.hidden = false;
      el.activeInitials.hidden = true;
      el.activeAvatar.onerror = () => {
        el.activeAvatar.hidden = true;
        el.activeInitials.hidden = false;
      };
    } else {
      el.activeAvatar.hidden = true;
      el.activeInitials.hidden = false;
    }
    el.activeInitials.textContent = initials;

    renderProfiles(candidate.profiles || []);
    renderEmail(candidate.email);
    renderCompany(payload.dossier || candidate.dossier);
    renderRoles(payload.roles || []);
    renderOutreach(payload.outreach || payload);
  }

  function renderProfiles(profiles) {
    if (!profiles.length) {
      el.activeProfiles.innerHTML = "";
      return;
    }
    el.activeProfiles.innerHTML = profiles
      .map(
        (profile) => `
        <li><a href="${esc(profile.url)}" target="_blank" rel="noopener noreferrer"
               data-verified="${profile.verified ? "true" : "false"}">
          ${esc(profile.kind.replace(/_/g, " "))}${profile.handle ? ` · ${esc(profile.handle)}` : ""}
        </a></li>`,
      )
      .join("");
  }

  function renderEmail(email) {
    if (!email || !email.address) {
      el.emailRow.hidden = true;
      return;
    }
    el.emailRow.hidden = false;
    el.emailLink.textContent = email.address;
    el.emailLink.href = `mailto:${email.address}`;
    el.emailStatus.textContent = email.status || "";
    el.emailStatus.dataset.status = email.status || "inferred";
    el.emailStatus.title = email.why || "";
  }

  function factRow(label, field) {
    const value = val(field);
    if (!value) return "";
    const source = srcOf(field);
    const rendered = source
      ? `<a href="${esc(source)}" target="_blank" rel="noopener noreferrer">${esc(value)}</a>`
      : esc(value);
    return `<div class="fact"><dt>${esc(label)}</dt><dd>${rendered}</dd></div>`;
  }

  function renderCompany(dossier) {
    const company = dossier?.company;
    if (!company) {
      el.companyBlock.hidden = true;
      return;
    }
    const rows = [
      factRow("Domain", company.domain),
      factRow("Site", company.site),
      factRow("Careers", company.careers_page),
      factRow("Blog", company.engineering_blog),
      factRow("GitHub", company.github_org),
      factRow("Stack", company.technology_stack),
      factRow("Funding", company.funding),
    ]
      .filter(Boolean)
      .join("");

    const news = Array.isArray(company.news)
      ? company.news
          .slice(0, 3)
          .map(
            (item) =>
              `<div class="fact"><dt>News</dt><dd>${
                srcOf(item)
                  ? `<a href="${esc(srcOf(item))}" target="_blank" rel="noopener noreferrer">${esc(
                      val(item),
                    )}</a>`
                  : esc(val(item))
              }</dd></div>`,
          )
          .join("")
      : "";

    if (!rows && !news) {
      el.companyBlock.hidden = true;
      return;
    }
    el.companyBlock.hidden = false;
    el.companyBody.innerHTML = rows + news;
  }

  function renderRoles(rolesPayload) {
    const roles = Array.isArray(rolesPayload)
      ? rolesPayload
      : Object.values(rolesPayload || {}).flatMap((entry) => entry.roles || []);
    if (!roles.length) {
      el.rolesBlock.hidden = true;
      return;
    }
    el.rolesBlock.hidden = false;
    el.rolesCount.textContent = String(roles.length);
    el.rolesBody.innerHTML = roles
      .slice(0, 6)
      .map(
        (role) => `
        <div class="role">
          <div class="role-top">
            <strong>${esc(role.title)}</strong>
            <span class="role-fit">${esc(role.fit_score ?? "")}${role.fit_score ? "%" : ""}</span>
          </div>
          <span class="meta">${esc(
            [role.team, role.location, role.ats].filter(Boolean).join(" · "),
          )}</span>
          ${
            role.canonical_apply_url
              ? `<a href="${esc(role.canonical_apply_url)}" target="_blank" rel="noopener noreferrer">View requisition ↗</a>`
              : ""
          }
        </div>`,
      )
      .join("");
  }

  /* ---------- outreach ---------- */

  function renderOutreach(payload) {
    const drafts = payload?.drafts || [];
    state.drafts = drafts;
    state.draftIndex = 0;
    state.variantIndex = 0;

    if (!drafts.length) {
      el.outreachBlock.hidden = true;
      return;
    }
    el.outreachBlock.hidden = false;
    el.outreachDecision.textContent = payload.decision || "";
    renderVariants();
  }

  function currentDraft() {
    return state.drafts[state.draftIndex] || null;
  }

  function currentVariant() {
    const draft = currentDraft();
    if (!draft) return null;
    const variants = draft.variants || [];
    return variants[state.variantIndex] || variants[0] || null;
  }

  function renderVariants() {
    const draft = currentDraft();
    if (!draft) return;
    const variants = draft.variants || [];

    el.variantTabs.innerHTML = variants
      .map(
        (variant, index) =>
          `<button type="button" role="tab" class="variant-tab"
             aria-selected="${index === state.variantIndex}"
             data-variant="${index}">${esc(variant.id || variant.tone || `v${index + 1}`)}</button>`,
      )
      .join("");

    const variant = currentVariant();
    el.draftBody.innerHTML = variant
      ? `<div class="subject">${esc(variant.subject || "(no subject)")}</div>
         <div class="body">${esc(variant.body || "")}</div>`
      : `<p class="fineprint tight">No draft prepared.</p>`;

    const note = [];
    if (draft.recipient) note.push(`To ${draft.recipient}`);
    if (draft.template) note.push(`${draft.template} template`);
    if (payloadSmtpOff()) note.push("SMTP off — opens your mail client instead");
    el.outreachNote.textContent = note.join(" · ");
  }

  let smtpConfigured = false;
  const payloadSmtpOff = () => !smtpConfigured;

  el.variantTabs.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-variant]");
    if (!tab) return;
    state.variantIndex = Number(tab.dataset.variant);
    renderVariants();
  });

  // Sending is owner-authenticated server-side. From the visitor page this
  // deliberately falls back to a prefilled compose window rather than pretending
  // it can dispatch mail on the owner's behalf.
  el.sendEmail.addEventListener("click", () => {
    const draft = currentDraft();
    const variant = currentVariant();
    if (!draft || !variant) return;
    const to = encodeURIComponent(draft.recipient || "");
    const subject = encodeURIComponent(variant.subject || "");
    const body = encodeURIComponent(variant.body || "");
    window.open(`mailto:${to}?subject=${subject}&body=${body}`, "_blank");
    toast("Opened your mail client with the draft.");
  });

  el.openLinkedin.addEventListener("click", () => {
    const profiles = state.active?.profiles || [];
    const linkedin = profiles.find((profile) => profile.kind === "linkedin");
    if (!linkedin) {
      toast("No public LinkedIn profile was observed.", "error");
      return;
    }
    window.open(linkedin.url, "_blank", "noopener");
  });

  /* ---------- chat ---------- */

  function addMessage(role, text, sources) {
    const wrapper = document.createElement("div");
    wrapper.className = `msg ${role}`;
    const chips = (sources || [])
      .map((source) => `<span class="src-chip">${esc(source)}</span>`)
      .join("");
    wrapper.innerHTML = `
      <span class="msg-role">${role === "twin" ? "PK.twin / verified" : "You"}</span>
      <div class="msg-body">${esc(text)}</div>
      ${chips ? `<div class="msg-sources">${chips}</div>` : ""}`;
    el.messages.appendChild(wrapper);
    el.messages.scrollTop = el.messages.scrollHeight;
    return wrapper;
  }

  function renderEvidence(sources) {
    el.sourceCount.textContent = String(sources.length).padStart(2, "0");
    el.evidenceList.innerHTML = sources.length
      ? sources
          .map(
            (source, index) => `
          <div class="evidence-item">
            <span class="label">Source / ${String(index + 1).padStart(2, "0")}</span>
            <span class="value">${esc(source)}</span>
          </div>`,
          )
          .join("")
      : `<p class="fineprint tight">Sources for the twin's latest answer appear here.</p>`;
  }

  async function sendMessage(text) {
    if (!text.trim() || state.busy || !state.sessionId) return;
    state.busy = true;
    el.send.disabled = true;
    addMessage("visitor", text);
    el.input.value = "";
    el.input.style.height = "auto";
    const pending = addMessage("twin", "Checking the evidence…");
    pending.classList.add("pending");

    try {
      const result = await api(`/api/sessions/${state.sessionId}/chat`, {
        method: "POST",
        body: JSON.stringify({ message: text }),
      });
      pending.remove();
      addMessage("twin", result.answer, result.sources);
      renderEvidence(result.sources || []);
      if (typeof result.budget_remaining === "number") {
        el.budget.textContent = `${result.budget_remaining.toLocaleString()} tokens left`;
      }
    } catch (error) {
      pending.remove();
      addMessage("twin", `I couldn't answer that: ${error.message}`);
    } finally {
      state.busy = false;
      el.send.disabled = false;
      el.input.focus();
    }
  }

  el.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage(el.input.value);
  });

  el.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage(el.input.value);
    }
  });

  el.input.addEventListener("input", () => {
    el.input.style.height = "auto";
    el.input.style.height = `${Math.min(el.input.scrollHeight, 160)}px`;
  });

  el.starters.innerHTML = STARTERS.map(
    (text) => `<button type="button" class="starter">${esc(text)}</button>`,
  ).join("");
  el.starters.addEventListener("click", (event) => {
    const button = event.target.closest(".starter");
    if (button) sendMessage(button.textContent);
  });

  /* ---------- SSE ---------- */

  function openEvents() {
    if (state.events) state.events.close();
    const source = new EventSource(`/api/sessions/${state.sessionId}/events`);
    state.events = source;

    source.addEventListener("research", (event) => {
      const payload = JSON.parse(event.data);
      renderResearchState(payload);
    });

    source.addEventListener("research.progress", (event) => {
      const payload = JSON.parse(event.data);
      state.sources.set(payload.source, payload);
      renderSources();
    });

    source.addEventListener("research.dossier", (event) => {
      const payload = JSON.parse(event.data);
      if (payload.candidates?.length) {
        state.candidates = payload.candidates;
        renderCandidates();
      }
    });

    source.addEventListener("roles.ready", (event) => {
      state.roles = JSON.parse(event.data).roles || {};
      if (state.active) renderRoles(state.roles);
    });

    source.addEventListener("outreach.ready", (event) => {
      renderOutreach(JSON.parse(event.data));
    });

    source.addEventListener("outreach.action", (event) => {
      const payload = JSON.parse(event.data);
      if (payload.status === "sent") toast("Outreach email sent.");
    });

    source.onerror = () => {
      /* EventSource reconnects on its own; a dropped stream must not break chat. */
    };
  }

  /* ---------- opt out ---------- */

  async function optOut() {
    if (!state.sessionId) return;
    try {
      await api(`/api/sessions/${state.sessionId}/research/opt-out`, { method: "POST" });
      state.candidates = [];
      state.active = null;
      state.sources.clear();
      renderResearchState({ status: "opted_out" });
      toast("Stopped. Everything found was erased.");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  [el.stripOptout, el.candidatesOptout, el.activeOptout].forEach((button) =>
    button.addEventListener("click", optOut),
  );

  el.stripView.addEventListener("click", () => {
    const rail = document.querySelector('.rail-state:not([hidden])');
    rail?.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  /* ---------- onboarding ---------- */

  el.identityForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = el.visitorName.value.trim();
    const company = el.visitorCompany.value.trim();
    el.onboarding.hidden = true;
    if (!name) return skip();
    try {
      await api(`/api/sessions/${state.sessionId}/identity`, {
        method: "POST",
        body: JSON.stringify({ name, company: company || null }),
      });
    } catch (error) {
      toast(error.message, "error");
    }
  });

  async function skip() {
    el.onboarding.hidden = true;
    try {
      await api(`/api/sessions/${state.sessionId}/skip`, { method: "POST" });
      renderResearchState({ status: "skipped" });
    } catch {
      /* Skipping must never block the conversation. */
    }
  }

  el.skipButton.addEventListener("click", skip);

  /* ---------- drawer: work + role fit ---------- */

  function openDrawer(title, html) {
    el.drawerTitle.textContent = title;
    el.drawerBody.innerHTML = html;
    el.drawer.hidden = false;
  }

  el.drawerClose.addEventListener("click", () => (el.drawer.hidden = true));
  el.drawer.addEventListener("click", (event) => {
    if (event.target === el.drawer) el.drawer.hidden = true;
  });

  el.projectsButton.addEventListener("click", async () => {
    openDrawer("Selected work", `<p class="fineprint tight">Loading live repository data…</p>`);
    try {
      const data = await api("/api/github");
      const repos = data.repositories || data.repos || [];
      openDrawer(
        "Selected work",
        repos
          .map(
            (repo) => `
          <div class="repo">
            <strong><a href="${esc(repo.url || repo.html_url)}" target="_blank" rel="noopener noreferrer">${esc(
              repo.name,
            )}</a></strong>
            <p>${esc(repo.description || "")}</p>
            ${
              repo.topics?.length
                ? `<div class="topics">${repo.topics
                    .map((topic) => `<span>${esc(topic)}</span>`)
                    .join("")}</div>`
                : ""
            }
          </div>`,
          )
          .join(""),
      );
    } catch (error) {
      openDrawer("Selected work", `<p class="fineprint tight">${esc(error.message)}</p>`);
    }
  });

  el.jdButton.addEventListener("click", () => {
    openDrawer(
      "Role fit",
      `<form class="jd-form" id="jd-form">
         <textarea id="jd-input" placeholder="Paste the job description…"></textarea>
         <div class="modal-actions"><button type="submit" class="primary-btn">Analyse</button></div>
       </form>
       <div id="jd-results"></div>`,
    );
    $("#jd-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const description = $("#jd-input").value.trim();
      if (!description) return;
      const results = $("#jd-results");
      results.innerHTML = `<p class="fineprint tight">Checking against the CV corpus…</p>`;
      try {
        const fit = await api(`/api/sessions/${state.sessionId}/jd-fit`, {
          method: "POST",
          body: JSON.stringify({ description }),
        });
        results.innerHTML = `
          <div class="repo"><strong>Summary</strong><p>${esc(fit.summary || "")}</p></div>
          ${(fit.matched || fit.matched_requirements || [])
            .map(
              (item) =>
                `<div class="repo"><strong>✓ ${esc(
                  item.requirement || item,
                )}</strong><p>${esc(item.source || "")}</p></div>`,
            )
            .join("")}
          ${(fit.unevidenced || fit.unevidenced_requirements || [])
            .map((item) => `<div class="repo"><strong>— ${esc(item.requirement || item)}</strong><p>Not evidenced in the CV.</p></div>`)
            .join("")}
          ${fit.caveat ? `<p class="fineprint">${esc(fit.caveat)}</p>` : ""}`;
      } catch (error) {
        results.innerHTML = `<p class="fineprint tight">${esc(error.message)}</p>`;
      }
    });
  });

  /* ---------- theme ---------- */

  const storedTheme = localStorage.getItem("twin-theme");
  if (storedTheme) document.documentElement.dataset.theme = storedTheme;

  el.themeButton.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("twin-theme", next);
  });

  /* ---------- boot ---------- */

  async function boot() {
    try {
      const health = await api("/api/health");
      el.providerLabel.textContent = health.grounding || "Grounding online";
      smtpConfigured = Boolean(health.smtp_configured);
    } catch {
      /* Health is decorative; the page still works without it. */
    }

    try {
      const contact = await api("/api/contact");
      const link = $("#contact-link");
      if (link && contact.email) {
        link.href = `mailto:${contact.email}`;
        link.textContent = "Email ↗";
      } else if (link) {
        link.remove();
      }
    } catch {
      $("#contact-link")?.remove();
    }

    try {
      const session = await api("/api/sessions", { method: "POST", body: "{}" });
      state.sessionId = session.session_id;
      addMessage("twin", session.greeting, ["CV › Grounding contract"]);
      renderResearchState(session.research || { status: "idle" });
      openEvents();
      el.onboarding.hidden = false;
      el.visitorName.focus();
    } catch (error) {
      addMessage("twin", `The twin could not start a session: ${error.message}`);
    }
  }

  boot();
})();
