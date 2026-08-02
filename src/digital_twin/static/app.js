(() => {
  "use strict";

  const state = {
    sessionId: null,
    eventSource: null,
    candidates: [],
    identityHandled: false,
    projectsLoaded: false,
    latestSources: [],
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const elements = {
    messages: $("#messages"),
    placeholder: $("#message-placeholder"),
    composer: $("#composer"),
    input: $("#composer-input"),
    send: $("#send-button"),
    starters: $("#starters"),
    onboarding: $("#onboarding"),
    identityForm: $("#identity-form"),
    skipName: $("#skip-name"),
    onboardingClose: $("#onboarding-close"),
    researchBar: $("#research-bar"),
    researchTitle: $("#research-title"),
    researchDisclosure: $("#research-disclosure"),
    researchReview: $("#research-review"),
    researchOptout: $("#research-optout"),
    candidateList: $("#candidate-list"),
    gateMap: $("#gate-map"),
    gateLock: $("#gate-lock"),
    evidenceList: $("#evidence-list"),
    sourceCount: $("#source-count"),
    backdrop: $("#drawer-backdrop"),
    jdForm: $("#jd-form"),
    jdInput: $("#jd-input"),
    jdResults: $("#jd-results"),
    projectGrid: $("#project-grid"),
    toastRegion: $("#toast-region"),
    providerLabel: $("#provider-label"),
  };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    if (response.status === 204) return null;
    let body = {};
    try {
      body = await response.json();
    } catch (_error) {
      body = { detail: "The server returned an unreadable response." };
    }
    if (!response.ok) throw new Error(body.detail || "Request failed");
    return body;
  }

  function toast(message) {
    const node = document.createElement("div");
    node.className = "toast";
    node.textContent = message;
    elements.toastRegion.append(node);
    window.setTimeout(() => node.remove(), 4300);
  }

  function closeOnboarding() {
    elements.onboarding.classList.add("hidden");
    elements.input.focus();
  }

  function addMessage(role, text, sources = [], refusal = false) {
    elements.placeholder?.remove();
    const article = document.createElement("article");
    article.className = `message ${role}${refusal ? " refusal" : ""}`;

    const meta = document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = role === "user" ? "YOU / VISITOR" : "PK.TWIN / VERIFIED";
    const body = document.createElement("div");
    body.className = "message-body";
    body.textContent = text;
    article.append(meta, body);

    if (sources.length) {
      const chips = document.createElement("div");
      chips.className = "source-chips";
      sources.forEach((source, index) => {
        const chip = document.createElement("button");
        chip.className = "source-chip";
        chip.type = "button";
        chip.textContent = `[${index + 1}] ${shortSource(source)}`;
        chip.addEventListener("click", () => {
          renderEvidence(sources);
          $(".evidence-card")?.scrollIntoView({ behavior: "smooth", block: "center" });
        });
        chips.append(chip);
      });
      article.append(chips);
    }
    elements.messages.append(article);
    elements.messages.scrollTop = elements.messages.scrollHeight;
    if (role === "assistant") renderEvidence(sources);
    return article;
  }

  function addTyping() {
    const node = addMessage("assistant", "");
    node.dataset.typing = "true";
    const body = $(".message-body", node);
    body.classList.add("typing");
    body.replaceChildren(...[0, 1, 2].map(() => document.createElement("i")));
    return node;
  }

  function shortSource(source) {
    if (source.startsWith("https://github.com/")) return source.split("/").pop();
    const parts = source.split(" › ");
    return parts.length > 2 ? parts.slice(-2).join(" › ") : source;
  }

  function renderEvidence(sources) {
    state.latestSources = sources;
    elements.sourceCount.textContent = String(sources.length).padStart(2, "0");
    elements.evidenceList.replaceChildren();
    if (!sources.length) {
      const empty = document.createElement("div");
      empty.className = "empty-evidence";
      const mark = document.createElement("span");
      mark.textContent = "↳";
      const copy = document.createElement("p");
      copy.textContent = "This response was a boundary refusal; it made no biographical claim.";
      empty.append(mark, copy);
      elements.evidenceList.append(empty);
      return;
    }
    sources.forEach((source, index) => {
      const node = document.createElement(source.startsWith("https://") ? "a" : "div");
      node.className = "evidence-item";
      if (node instanceof HTMLAnchorElement) {
        node.href = source;
        node.target = "_blank";
        node.rel = "noopener noreferrer";
      }
      const number = document.createElement("span");
      number.textContent = `SOURCE / ${String(index + 1).padStart(2, "0")}`;
      const label = document.createElement("p");
      label.textContent = source;
      node.append(number, label);
      elements.evidenceList.append(node);
    });
  }

  function openDrawer(id) {
    $$(".drawer.open").forEach((drawer) => {
      drawer.classList.remove("open");
      drawer.setAttribute("aria-hidden", "true");
    });
    const drawer = $(`#${id}`);
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    elements.backdrop.classList.remove("hidden");
    $("button", drawer)?.focus();
  }

  function closeDrawers() {
    $$(".drawer.open").forEach((drawer) => {
      drawer.classList.remove("open");
      drawer.setAttribute("aria-hidden", "true");
    });
    elements.backdrop.classList.add("hidden");
  }

  function handleResearch(event) {
    const status = event.status || "idle";
    elements.researchBar.dataset.state = status;
    elements.researchTitle.textContent = event.message || researchTitle(status);
    elements.researchDisclosure.textContent = event.disclosure || "Public sources only.";
    elements.researchReview.classList.toggle("hidden", status !== "candidates");
    elements.researchOptout.classList.toggle(
      "hidden",
      !["researching", "candidates", "confirmed"].includes(status),
    );
    if (status === "candidates") {
      state.candidates = event.candidates || [];
      renderCandidates(state.candidates);
      openDrawer("research-drawer");
    }
    if (status === "confirmed") {
      elements.gateMap.dataset.state = "open";
      elements.gateLock.textContent = "AUTHORISED";
      closeDrawers();
      toast("Context confirmed. Tailoring is now enabled.");
    }
    if (["skipped", "opted_out", "empty"].includes(status)) {
      elements.gateMap.dataset.state = "locked";
      elements.gateLock.textContent = "LOCKED";
    }
  }

  function researchTitle(status) {
    const labels = {
      idle: "Public context gate",
      researching: "Researching public sources…",
      candidates: "Possible matches need your confirmation",
      confirmed: "Public context explicitly confirmed",
      skipped: "Research skipped — full chat active",
      opted_out: "Research stopped and purged",
      empty: "No useful public match found",
    };
    return labels[status] || "Public context gate";
  }

  function renderCandidates(candidates) {
    elements.candidateList.replaceChildren();
    if (!candidates.length) {
      const empty = document.createElement("p");
      empty.className = "drawer-intro";
      empty.textContent = "No useful candidates were returned. Chat is unaffected.";
      elements.candidateList.append(empty);
      return;
    }
    candidates.forEach((candidate) => {
      const card = document.createElement("article");
      card.className = "candidate";
      const top = document.createElement("div");
      top.className = "candidate-top";
      let avatar;
      if (candidate.photo_url) {
        avatar = document.createElement("img");
        avatar.src = candidate.photo_url;
        avatar.alt = "Public profile image";
        avatar.referrerPolicy = "no-referrer";
        avatar.addEventListener("error", () => {
          const fallback = avatarNode(candidate.initials);
          avatar.replaceWith(fallback);
        });
      } else {
        avatar = avatarNode(candidate.initials);
      }
      avatar.classList.add("candidate-avatar");
      const copy = document.createElement("div");
      copy.className = "candidate-copy";
      const name = document.createElement("strong");
      name.textContent = candidate.name;
      const headline = document.createElement("span");
      headline.textContent = [candidate.headline, candidate.company].filter(Boolean).join(" · ");
      copy.append(name, headline);
      const confidence = document.createElement("span");
      confidence.className = "confidence";
      confidence.textContent = `${candidate.confidence}%`;
      confidence.title = "Computed confidence, not model-generated";
      top.append(avatar, copy, confidence);

      const why = document.createElement("div");
      why.className = "candidate-why";
      why.textContent = `Why ${candidate.confidence}%: ${(candidate.why || []).join(" · ")}`;
      const bottom = document.createElement("div");
      bottom.className = "candidate-bottom";
      const source = document.createElement("a");
      source.className = "candidate-source";
      source.href = candidate.source_link;
      source.target = "_blank";
      source.rel = "noopener noreferrer";
      source.textContent = `${candidate.source_label} ↗`;
      const confirm = document.createElement("button");
      confirm.className = "confirm-button";
      confirm.type = "button";
      confirm.textContent = "THIS IS ME → CONFIRM";
      confirm.addEventListener("click", () => confirmCandidate(candidate.id, confirm));
      bottom.append(source, confirm);
      card.append(top, why, bottom);
      elements.candidateList.append(card);
    });
  }

  function avatarNode(initials) {
    const avatar = document.createElement("div");
    avatar.textContent = initials || "?";
    return avatar;
  }

  async function confirmCandidate(id, button) {
    button.disabled = true;
    button.textContent = "CONFIRMING…";
    try {
      const result = await api(`/api/sessions/${state.sessionId}/confirm`, {
        method: "POST",
        body: JSON.stringify({ candidate_id: id }),
      });
      handleResearch({
        status: "confirmed",
        message: `Confirmed ${result.candidate.name}. Context tailoring is active.`,
        disclosure: "You authorised this context. Stop & purge remains available.",
      });
    } catch (error) {
      toast(error.message);
      button.disabled = false;
      button.textContent = "THIS IS ME → CONFIRM";
    }
  }

  async function submitIdentity(skip = false) {
    if (state.identityHandled || !state.sessionId) return;
    state.identityHandled = true;
    const name = skip ? "" : $("#visitor-name").value.trim();
    const company = skip ? "" : $("#visitor-company").value.trim();
    closeOnboarding();
    try {
      const path = skip || !name
        ? `/api/sessions/${state.sessionId}/skip`
        : `/api/sessions/${state.sessionId}/identity`;
      const result = await api(path, {
        method: "POST",
        body: JSON.stringify(skip || !name ? {} : { name, company: company || null }),
      });
      if (result.status === "skipped") {
        handleResearch({
          status: "skipped",
          message: "Research skipped — full chat active",
          disclosure: "Nothing was looked up. Every conversation feature remains available.",
        });
      }
    } catch (error) {
      toast(`Could not start research: ${error.message}. Chat is still available.`);
    }
  }

  async function sendMessage(text) {
    const message = text.trim();
    if (!message || !state.sessionId || elements.send.disabled) return;
    elements.input.value = "";
    resizeInput();
    elements.starters.classList.add("hidden");
    addMessage("user", message);
    const typing = addTyping();
    elements.send.disabled = true;
    try {
      const result = await api(`/api/sessions/${state.sessionId}/chat`, {
        method: "POST",
        body: JSON.stringify({ message }),
      });
      typing.remove();
      addMessage("assistant", result.answer, result.sources, result.refusal);
      if (result.tailored_for) toast(`Answer tailored to confirmed context: ${result.tailored_for}`);
    } catch (error) {
      typing.remove();
      addMessage("assistant", error.message, [], true);
    } finally {
      elements.send.disabled = false;
      elements.input.focus();
    }
  }

  function resizeInput() {
    elements.input.style.height = "auto";
    elements.input.style.height = `${Math.min(elements.input.scrollHeight, 150)}px`;
  }

  async function optOut() {
    try {
      await api(`/api/sessions/${state.sessionId}/research/opt-out`, {
        method: "POST",
        body: "{}",
      });
      state.candidates = [];
      handleResearch({
        status: "opted_out",
        message: "Research stopped and purged",
        disclosure: "No research context is available to this conversation.",
      });
      closeDrawers();
      toast("Session research was purged.");
    } catch (error) {
      toast(error.message);
    }
  }

  async function runJobFit(event) {
    event.preventDefault();
    const description = elements.jdInput.value.trim();
    if (description.length < 20) return;
    const button = $("button[type=submit]", elements.jdForm);
    button.disabled = true;
    button.firstChild.textContent = "Comparing evidence… ";
    try {
      const result = await api(`/api/sessions/${state.sessionId}/jd-fit`, {
        method: "POST",
        body: JSON.stringify({ description }),
      });
      renderJobFit(result);
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
      button.firstChild.textContent = "Run evidence comparison ";
    }
  }

  function renderJobFit(result) {
    elements.jdResults.replaceChildren();
    const score = document.createElement("div");
    score.className = "fit-score";
    const number = document.createElement("strong");
    number.textContent = `${result.coverage_percent}%`;
    const summary = document.createElement("p");
    summary.textContent = result.summary;
    score.append(number, summary);
    elements.jdResults.append(score);

    const matched = fitSection("DIRECTLY EVIDENCED");
    result.matched.forEach((item) => {
      const row = document.createElement("div");
      row.className = "fit-row";
      const title = document.createElement("strong");
      title.textContent = `${item.requirement} · ${shortSource(item.source)}`;
      const evidence = document.createElement("p");
      evidence.textContent = item.evidence;
      row.append(title, evidence);
      matched.append(row);
    });
    if (!result.matched.length) matched.append(document.createTextNode("No direct matches detected."));
    elements.jdResults.append(matched);

    const gaps = fitSection("NOT EVIDENCED IN THE CV");
    const tags = document.createElement("div");
    tags.className = "gap-tags";
    result.not_evidenced.forEach((gap) => {
      const tag = document.createElement("span");
      tag.textContent = gap;
      tags.append(tag);
    });
    if (!result.not_evidenced.length) tags.append(document.createTextNode("No recognised gaps detected."));
    gaps.append(tags);
    elements.jdResults.append(gaps);
    const caveat = document.createElement("p");
    caveat.className = "fit-caveat";
    caveat.textContent = result.caveat;
    elements.jdResults.append(caveat);
  }

  function fitSection(title) {
    const section = document.createElement("section");
    section.className = "fit-section";
    const heading = document.createElement("h3");
    heading.textContent = title;
    section.append(heading);
    return section;
  }

  async function loadProjects() {
    openDrawer("projects-drawer");
    if (state.projectsLoaded) return;
    try {
      const result = await api("/api/github");
      elements.projectGrid.replaceChildren();
      result.repositories.forEach((repo) => {
        const card = document.createElement("a");
        card.className = "project-card";
        card.href = repo.url;
        card.target = "_blank";
        card.rel = "noopener noreferrer";
        const top = document.createElement("div");
        top.className = "project-top";
        const name = document.createElement("strong");
        name.textContent = repo.name;
        const live = document.createElement("span");
        live.className = `live-badge${repo.live ? "" : " offline"}`;
        live.textContent = repo.live ? "● LIVE" : "○ OFFLINE";
        top.append(name, live);
        const description = document.createElement("p");
        description.textContent = repo.description || "No description supplied.";
        const stats = document.createElement("div");
        stats.className = "repo-stats";
        stats.textContent = repo.live
          ? `☆ ${repo.stars} ⑂ ${repo.forks} ◌ ${repo.language || "n/a"}`
          : "Live stats unavailable";
        const topics = document.createElement("div");
        topics.className = "repo-topics";
        (repo.topics || []).slice(0, 5).forEach((topic) => {
          const tag = document.createElement("span");
          tag.textContent = `#${topic}`;
          topics.append(tag);
        });
        card.append(top, description, stats, topics);
        elements.projectGrid.append(card);
      });
      state.projectsLoaded = true;
    } catch (error) {
      elements.projectGrid.textContent = `Live metadata unavailable: ${error.message}`;
    }
  }

  function connectEvents() {
    state.eventSource?.close();
    state.eventSource = new EventSource(`/api/sessions/${state.sessionId}/events`);
    state.eventSource.addEventListener("research", (event) => {
      try {
        handleResearch(JSON.parse(event.data));
      } catch (_error) {
        // Malformed event data is ignored; it never reaches model context.
      }
    });
  }

  async function initialise() {
    try {
      const [session, health] = await Promise.all([
        api("/api/sessions", { method: "POST", body: "{}" }),
        api("/api/health"),
      ]);
      state.sessionId = session.session_id;
      elements.providerLabel.textContent = health.provider === "scripted" ? "OFFLINE SAFE" : "MODEL ONLINE";
      addMessage("assistant", session.greeting, ["Policy › Grounding boundary"]);
      connectEvents();
      $("#visitor-name").focus();
    } catch (error) {
      elements.placeholder.textContent = `Could not start a session: ${error.message}`;
      elements.onboarding.classList.add("hidden");
      elements.send.disabled = true;
    }
  }

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $("#theme-icon").textContent = theme === "dark" ? "☼" : "◐";
    localStorage.setItem("twin-theme", theme);
  }

  elements.identityForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitIdentity(false);
  });
  elements.skipName.addEventListener("click", () => submitIdentity(true));
  elements.onboardingClose.addEventListener("click", () => submitIdentity(true));
  elements.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage(elements.input.value);
  });
  elements.input.addEventListener("input", resizeInput);
  elements.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      elements.composer.requestSubmit();
    }
  });
  $$('[data-prompt]').forEach((button) => button.addEventListener("click", () => sendMessage(button.dataset.prompt)));
  elements.researchReview.addEventListener("click", () => openDrawer("research-drawer"));
  elements.researchOptout.addEventListener("click", optOut);
  $("#jd-button").addEventListener("click", () => openDrawer("jd-drawer"));
  $("#projects-button").addEventListener("click", loadProjects);
  elements.jdForm.addEventListener("submit", runJobFit);
  elements.backdrop.addEventListener("click", closeDrawers);
  $$('[data-close-drawer]').forEach((button) => button.addEventListener("click", closeDrawers));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawers();
  });
  $("#theme-button").addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });

  const savedTheme = localStorage.getItem("twin-theme");
  setTheme(savedTheme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  if (location.pathname === "/embed") document.body.classList.add("embed-mode");
  window.addEventListener("pagehide", () => {
    state.eventSource?.close();
    if (state.sessionId) {
      fetch(`/api/sessions/${state.sessionId}`, { method: "DELETE", keepalive: true }).catch(() => {});
    }
  });
  initialise();
})();
