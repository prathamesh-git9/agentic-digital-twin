(() => {
  "use strict";
  const rows = document.querySelector("#owner-rows");
  const metrics = document.querySelector("#owner-metrics");
  const updated = document.querySelector("#owner-updated");

  function metric(label, value) {
    const card = document.createElement("div");
    card.className = "owner-metric";
    const name = document.createElement("span");
    name.textContent = label;
    const number = document.createElement("strong");
    number.textContent = value;
    card.append(name, number);
    return card;
  }

  function cell(value) {
    const td = document.createElement("td");
    if (value instanceof Node) td.append(value);
    else td.textContent = value;
    return td;
  }

  fetch("/api/owner/visits", { credentials: "same-origin" })
    .then((response) => {
      if (!response.ok) throw new Error(`Dashboard request failed (${response.status})`);
      return response.json();
    })
    .then(({ visits }) => {
      const confirmed = visits.filter((visit) => visit.research_status === "confirmed").length;
      const questions = visits.reduce((sum, visit) => sum + visit.questions.length, 0);
      const tokens = visits.reduce((sum, visit) => sum + visit.token_usage, 0);
      metrics.append(
        metric("TOTAL SESSIONS", visits.length),
        metric("QUESTIONS", questions),
        metric("CONFIRMED CONTEXTS", confirmed),
        metric("EST. TOKENS", tokens.toLocaleString()),
      );
      rows.replaceChildren();
      visits.forEach((visit) => {
        const tr = document.createElement("tr");
        const visitor = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = visit.visitor_name || "Anonymous visitor";
        const company = document.createElement("span");
        company.textContent = visit.visitor_company || "Company not supplied";
        visitor.append(name, company);
        const status = document.createElement("span");
        status.className = "status-pill";
        status.textContent = visit.research_status;
        const list = document.createElement("ul");
        list.className = "question-list";
        (visit.questions.length ? visit.questions : ["No questions yet"]).forEach((question) => {
          const item = document.createElement("li");
          item.textContent = question;
          list.append(item);
        });
        tr.append(
          cell(visitor),
          cell(status),
          cell(list),
          cell(`${visit.message_count} msg · ${visit.token_usage} tok`),
          cell(new Date(visit.created_at).toLocaleString()),
        );
        rows.append(tr);
      });
      if (!visits.length) rows.append(cell("No visits recorded."));
      updated.textContent = new Date().toLocaleTimeString();
    })
    .catch((error) => {
      rows.textContent = error.message;
      updated.textContent = "ERROR";
    });
})();
