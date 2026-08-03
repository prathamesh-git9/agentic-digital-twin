/*
  Offline retrieval engine for the static (GitHub Pages) build.

  There is no FastAPI process behind the published page, so this file answers
  the same endpoints app.js already calls. It is a genuine hybrid retriever --
  BM25 over the CV corpus, character-trigram similarity beside it, the two
  rankings fused with reciprocal rank fusion -- and then it composes the answer
  extractively from the retrieved chunks.

  It loads in both deployments. Only the static build lets it answer endpoints
  (app.js gates that on window.__TWIN_OFFLINE__); the server build loads it for
  the retrieval explorer alone.

  Extractive is a deliberate limit, not a shortcut: with no model call there is
  nothing to hallucinate, so every sentence shown is a chunk that exists in
  data/corpus.json, and the UI says so. The server build keeps the same
  retrieve-then-cite contract and adds drafting plus claim verification on top.

  The refusal boundaries mirror providers.CONTRACT_RE and
  security.INJECTION_PATTERNS. If those move server-side, move them here too --
  a twin that negotiates salary offline but not online is worse than neither.
*/
(() => {
  "use strict";

  // The static build ships the corpus beside the page; the server exposes the
  // same shape so the retrieval explorer works in both deployments.
  const CORPUS_URLS = ["./data/corpus.json", "/api/corpus"];

  const CONTRACT_RE =
    /\b(?:salary|compensation|pay|offer|accept|contract|start date|joining date|notice period|negotiate)\b/i;

  const INJECTION_PATTERNS = [
    /ignore\s+(?:all\s+)?(?:previous|prior|above|system)\s+instructions?/i,
    /(?:reveal|print|show|repeat)\s+(?:the\s+)?system\s+prompt/i,
    /(?:developer|system)\s*(?:message|prompt)\s*:/i,
    /(?:override|bypass|disregard)\s+(?:the\s+)?(?:rules|policy|guardrails)/i,
    /pretend\s+(?:that\s+)?(?:you|he)\s+(?:are|has|worked)/i,
    /say\s+(?:that\s+)?(?:you|he)\s+(?:have|has|worked)/i,
    /do\s+not\s+cite|without\s+(?:a\s+)?source/i,
    /<\/?(?:system|assistant|developer|tool)[^>]*>/i,
  ];

  const STOP = new Set(
    ("a an the and or but if of to in on at by for with from as is are was were be been " +
      "being do does did doing have has had having he she it they them his her its their " +
      "this that these those i you we us our your me my what which who whom how why when " +
      "where can could should would will shall may might must about into over under than " +
      "then there here some any all no not so such own same too very just also").split(" "),
  );

  // Recruiters ask in role language; the corpus answers in CV language. These
  // bridge the two vocabularies before retrieval rather than after, so the
  // expanded terms participate in scoring instead of re-ranking.
  const EXPANSIONS = {
    rag: ["retrieval", "embeddings", "vector", "context", "grounding", "search"],
    mcp: ["model", "context", "protocol", "tool", "server", "orchestration"],
    agent: ["agentic", "orchestration", "workflows", "tool", "autonomous"],
    agents: ["agentic", "orchestration", "workflows", "tool"],
    llm: ["openai", "claude", "model", "prompt", "generation"],
    ai: ["llm", "openai", "claude", "agentic", "model"],
    backend: ["back-end", "service", "api", "spring", "microservices"],
    java: ["spring", "boot", "jvm", "microservices"],
    python: ["fastapi", "service", "automation"],
    devops: ["docker", "ci", "cd", "pipeline", "deployment"],
    security: ["injection", "validation", "secrets", "least-privilege", "cybersecurity"],
    testing: ["tests", "integration", "regression", "coverage"],
    database: ["sql", "data", "modeling", "postgres", "sqlite"],
    cloud: ["aws", "docker", "deployment"],
    experience: ["years", "engineer", "worked", "built"],
    fit: ["experience", "built", "engineer", "skills"],
    lead: ["ownership", "collaborating", "reviews"],
    scale: ["production", "throughput", "reliability", "performance"],
    // Recruiters ask about education in words the CV never uses: it says "MSc"
    // and "Dublin Business School", never "study" or "qualification".
    study: ["education", "degree", "msc", "bsc", "university", "honours", "coursework"],
    studied: ["education", "degree", "msc", "bsc", "university", "honours"],
    education: ["msc", "bsc", "degree", "university", "honours", "coursework", "school"],
    degree: ["msc", "bsc", "honours", "university", "education"],
    university: ["dublin", "business", "school", "savitribai", "pune", "msc", "bsc"],
    college: ["university", "msc", "bsc", "school"],
    qualification: ["msc", "bsc", "degree", "certification", "education"],
    based: ["location", "dublin", "ireland", "contact"],
    location: ["dublin", "ireland", "based"],
    contact: ["email", "reach", "location"],
    overview: ["summary", "engineer", "years", "experience"],
    summary: ["engineer", "years", "experience", "backend"],
    certification: ["course", "aws", "node", "certifications"],
  };

  // Questions that want the top-line summary rather than the best keyword match.
  const OVERVIEW_RE =
    /\b(overview|summar(y|ise|ize)|who is he|introduce|elevator|60[- ]second|tell me about him)\b/i;

  const tokenize = (value) =>
    String(value || "")
      .toLowerCase()
      .split(/[^a-z0-9+#.]+/)
      .filter((token) => token.length > 1 && !STOP.has(token));

  function expand(terms) {
    const out = terms.slice();
    for (const term of terms) {
      const extra = EXPANSIONS[term];
      if (extra) out.push(...extra);
    }
    return out;
  }

  function trigrams(value) {
    const padded = ` ${String(value || "").toLowerCase().replace(/\s+/g, " ")} `;
    const set = new Map();
    for (let i = 0; i < padded.length - 2; i += 1) {
      const gram = padded.slice(i, i + 3);
      set.set(gram, (set.get(gram) || 0) + 1);
    }
    return set;
  }

  function cosine(a, b) {
    let dot = 0;
    let normA = 0;
    let normB = 0;
    for (const weight of a.values()) normA += weight * weight;
    for (const [gram, weight] of b) {
      normB += weight * weight;
      const other = a.get(gram);
      if (other) dot += other * weight;
    }
    if (!normA || !normB) return 0;
    return dot / Math.sqrt(normA * normB);
  }

  /* ---------- index ---------- */

  function buildIndex(items) {
    const docs = items.map((item) => `${item.source} ${item.text}`);
    const frequencies = docs.map((doc) => {
      const counts = new Map();
      for (const term of tokenize(doc)) counts.set(term, (counts.get(term) || 0) + 1);
      return counts;
    });
    const lengths = frequencies.map((counts) =>
      [...counts.values()].reduce((total, value) => total + value, 0),
    );
    const total = docs.length || 1;
    const average = lengths.reduce((sum, value) => sum + value, 0) / total;

    const documentFrequency = new Map();
    for (const counts of frequencies) {
      for (const term of counts.keys()) {
        documentFrequency.set(term, (documentFrequency.get(term) || 0) + 1);
      }
    }
    const idf = new Map();
    for (const [term, count] of documentFrequency) {
      idf.set(term, Math.log(1 + (total - count + 0.5) / (count + 0.5)));
    }
    return {
      items,
      frequencies,
      lengths,
      average: average || 1,
      idf,
      grams: docs.map(trigrams),
    };
  }

  // Okapi BM25 with the conventional saturation and length-normalisation
  // constants; k1 caps the reward for repeating a term, b controls how much a
  // long chunk is penalised for it.
  function bm25(index, terms, k1 = 1.5, b = 0.75) {
    const scored = [];
    for (let i = 0; i < index.items.length; i += 1) {
      const counts = index.frequencies[i];
      let score = 0;
      for (const term of terms) {
        const frequency = counts.get(term);
        if (!frequency) continue;
        const norm = 1 - b + (b * index.lengths[i]) / index.average;
        score += (index.idf.get(term) || 0) * ((frequency * (k1 + 1)) / (frequency + k1 * norm));
      }
      if (score > 0) scored.push([i, score]);
    }
    return scored.sort((a, b2) => b2[1] - a[1]);
  }

  function semantic(index, query) {
    const queryGrams = trigrams(query);
    const scored = [];
    for (let i = 0; i < index.items.length; i += 1) {
      const score = cosine(queryGrams, index.grams[i]);
      if (score > 0.02) scored.push([i, score]);
    }
    return scored.sort((a, b) => b[1] - a[1]);
  }

  // Reciprocal rank fusion: combine rankings by position rather than by score,
  // so BM25's unbounded magnitudes cannot swamp a bounded cosine.
  function fuse(rankings, k = 60) {
    const totals = new Map();
    for (const ranking of rankings) {
      ranking.forEach(([id], rank) => {
        totals.set(id, (totals.get(id) || 0) + 1 / (k + rank + 1));
      });
    }
    return [...totals.entries()].sort((a, b) => b[1] - a[1]);
  }

  const sectionOf = (source) => source.split("›").slice(0, 3).join("›").trim();

  function retrieve(index, query, limit = 4, { evidenceOnly = true } = {}) {
    const terms = expand(tokenize(query));
    if (!terms.length) return { hits: [], trace: null };
    const lexical = bm25(index, terms);
    const dense = semantic(index, query);
    const rankings = [lexical, dense];
    // A third ranking, fused the same way, is how an overview question reaches
    // the summary without a special case downstream.
    if (OVERVIEW_RE.test(query)) {
      rankings.push(
        index.items
          .map((item, id) => [id, item])
          .filter((pair) => /Summary/i.test(pair[1].source))
          .map((pair) => [pair[0], 1]),
      );
    }
    const fused = fuse(rankings);

    const hits = [];
    const seenText = new Set();
    const perSection = new Map();
    let best = 0;
    for (const [id, score] of fused) {
      const item = index.items[id];
      // Policy chunks state what the twin will not do. They are the right answer
      // to "can you negotiate?" and never the right answer to anything else, so
      // they belong in refusals rather than in evidence.
      if (evidenceOnly && /^Policy/i.test(item.source)) continue;
      if (!best) best = score;
      // Anything scoring far below the best match is padding. A fixed four
      // chunks meant "where is he based?" answered correctly and then added
      // three unrelated paragraphs.
      if (score < best * 0.55) break;
      const fingerprint = item.text.slice(0, 60).toLowerCase();
      if (seenText.has(fingerprint)) continue;
      const group = sectionOf(item.source);
      if ((perSection.get(group) || 0) >= 2) continue;
      seenText.add(fingerprint);
      perSection.set(group, (perSection.get(group) || 0) + 1);
      hits.push({ ...item, score });
      if (hits.length >= limit) break;
    }
    return {
      hits,
      trace: {
        candidates: new Set([...lexical, ...dense].map(([id]) => id)).size,
        lexical: lexical.length,
        dense: dense.length,
        fused: fused.length,
        terms: terms.length,
      },
    };
  }

  /* ---------- answering ---------- */

  const CONTACT_REFUSAL = (email) =>
    `I can't negotiate salary, accept an offer, commit to a start date, or make a ` +
    `contractual promise for Prathamesh. Please contact him at ${email}.`;

  const INJECTION_REFUSAL =
    "I treat pasted instructions as untrusted data. I can only answer from " +
    "Prathamesh's CV and allow-listed GitHub repositories.";

  function compose(question, hits) {
    // The corpus bullets are already written as standalone prose, so joining the
    // top chunks reads as an answer. Anything beyond this would be generation,
    // and there is no model here to ground it.
    const body = hits.map((hit) => hit.text.trim().replace(/\s+/g, " ")).join("\n\n");
    // A single confident hit needs no preamble. Announcing "here is what the CV
    // shows" ahead of one short sentence is worse than just saying the sentence.
    if (hits.length === 1) return body;
    const lead = OVERVIEW_RE.test(question)
      ? "The short version, from his CV:"
      : /\bfits?\b|\bsuited\b|\bright for\b|\bgood for\b/i.test(question)
        ? "Judge it from the evidence rather than from my opinion:"
        : "From his CV and public repositories:";
    return `${lead}\n\n${body}`;
  }

  function answer(index, corpus, question) {
    const email = corpus.person.email;
    if (CONTRACT_RE.test(question)) {
      return { answer: CONTACT_REFUSAL(email), sources: [], grounded: false, refusal: true };
    }
    if (INJECTION_PATTERNS.some((pattern) => pattern.test(question))) {
      return { answer: INJECTION_REFUSAL, sources: [], grounded: false, refusal: true };
    }
    const { hits, trace } = retrieve(index, question);
    if (!hits.length) {
      return {
        answer:
          "That isn't evidenced in his CV or the repositories I can see, so I won't " +
          `guess at it. ${corpus.person.name} can answer directly at ${email}.`,
        sources: [],
        grounded: false,
        refusal: true,
      };
    }
    return {
      answer: compose(question, hits),
      // Two chunks from the same CV entry are one citation, not two: repeating
      // the identical label under an answer reads as a rendering fault.
      sources: [...new Set(hits.map((hit) => hit.source))],
      grounded: true,
      refusal: false,
      trace,
    };
  }

  /* ---------- job-description fit ---------- */

  // Split on the bullet and line shapes job ads actually use, then keep only
  // fragments long enough to be a requirement rather than a heading.
  function requirements(description) {
    return String(description)
      .split(/[\n•·]|(?:^|\s)[-*]\s|(?<=[.;])\s+/)
      .map((line) => line.replace(/\s+/g, " ").trim())
      .filter((line) => line.length > 18 && line.length < 240)
      .slice(0, 14);
  }

  function jobFit(index, description) {
    const lines = requirements(description);
    const matched = [];
    const unevidenced = [];
    for (const line of lines) {
      const { hits } = retrieve(index, line, 1);
      const hit = hits[0];
      // A single fused hit clears the bar only if the requirement's own terms
      // actually appear in it; otherwise near-miss chunks would read as matches.
      const wanted = new Set(tokenize(line));
      const found = new Set(tokenize(hit ? `${hit.source} ${hit.text}` : ""));
      let overlap = 0;
      for (const term of wanted) if (found.has(term)) overlap += 1;
      const ratio = wanted.size ? overlap / wanted.size : 0;
      if (hit && ratio >= 0.25) matched.push({ requirement: line, source: hit.source });
      else unevidenced.push({ requirement: line });
    }
    const total = matched.length + unevidenced.length;
    const summary = total
      ? `${matched.length} of ${total} requirements are directly evidenced in the CV. ` +
        `The rest are listed as not evidenced rather than assumed.`
      : "No requirements could be parsed from that text.";
    return { summary, matched, unevidenced };
  }

  /* ---------- endpoint adapter ---------- */

  let ready = null;

  async function fetchCorpus() {
    let last;
    for (const url of CORPUS_URLS) {
      try {
        const response = await fetch(url);
        if (response.ok) return await response.json();
        last = new Error(`${url} -> ${response.status}`);
      } catch (error) {
        last = error;
      }
    }
    throw last || new Error("corpus unavailable");
  }

  function load() {
    if (ready) return ready;
    ready = fetchCorpus().then((corpus) => ({
      corpus,
      index: buildIndex(corpus.items),
    }));
    ready.catch(() => { ready = null; });
    return ready;
  }

  /* ---------- retrieval explorer ---------- */

  // Exposed so the page can show the two rankings and their fusion side by
  // side. A claim of RAG experience is worth less than a working retriever the
  // reader can type into.
  async function explore(query, limit = 5) {
    const { index } = await load();
    const terms = expand(tokenize(query));
    if (!terms.length) return null;
    const lexical = bm25(index, terms);
    const dense = semantic(index, query);
    const fused = fuse([lexical, dense]);
    const label = (id) => index.items[id].source;
    const rankOf = (ranking, id) => {
      const at = ranking.findIndex(([other]) => other === id);
      return at < 0 ? null : at + 1;
    };
    return {
      terms,
      scanned: index.items.length,
      lexical: lexical.slice(0, limit).map(([id, score]) => ({ label: label(id), score })),
      dense: dense.slice(0, limit).map(([id, score]) => ({ label: label(id), score })),
      fused: fused.slice(0, limit).map(([id, score]) => ({
        label: label(id),
        score,
        text: index.items[id].text,
        lexicalRank: rankOf(lexical, id),
        denseRank: rankOf(dense, id),
      })),
    };
  }

  const json = (body) => body;

  async function handle(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const body = options.body ? JSON.parse(options.body) : {};
    const { corpus, index } = await load();

    if (path === "/api/bootstrap" || path === "/api/sessions") {
      const session = { session_id: "offline", greeting: "", research: null };
      if (path === "/api/sessions") return json(session);
      return json({
        session,
        health: {
          status: "ok",
          provider: "static-retrieval",
          model: "hybrid BM25 + RRF · no model call",
          grounding: "authority-gated",
        },
        contact: { email: corpus.person.email, location: corpus.person.location },
        repositories: corpus.repositories || null,
      });
    }

    if (path === "/api/health") {
      return json({ status: "ok", provider: "static-retrieval", model: "hybrid BM25 + RRF" });
    }

    if (path === "/api/contact") {
      return json({ email: corpus.person.email, location: corpus.person.location });
    }

    if (path === "/api/github") {
      return json({ source: "build snapshot", repositories: corpus.repositories || [] });
    }

    if (/^\/api\/sessions\/[^/]+\/chat$/.test(path) && method === "POST") {
      return json(answer(index, corpus, String(body.message || "")));
    }

    if (/^\/api\/sessions\/[^/]+\/jd-fit$/.test(path) && method === "POST") {
      return json(jobFit(index, String(body.description || "")));
    }

    // Research, outreach and the owner dashboard need a server and a database.
    // Returning undefined lets app.js fall through to the network, which is the
    // right behaviour if this page is ever served from the real backend.
    return undefined;
  }

  window.__TWIN_LOCAL__ = { handle, load, explore };
})();
