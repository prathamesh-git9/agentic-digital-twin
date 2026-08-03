# Digital Twin API

This is the backend contract for the standalone page, embedded widget, and owner tools.
The interactive OpenAPI document is also available at `/docs`.

## Conventions and authority boundary

- JSON is used for requests and responses except SSE, static assets, and the CSV export.
- Session IDs are UUIDs. An unknown or purged session returns `404`.
- Rate-limited operations return `429`; `Retry-After` is included where relevant.
- Visitor research is card/outreach data before confirmation. The context assembler has no
  input path for candidate or dossier data until `POST /api/sessions/{id}/confirm` succeeds.
- Confirmation authorises research context for chat and outreach preparation. The current
  owner send policy is separately configuration-driven and does not wait for confirmation.
- `/owner`, `/outreach/approve`, `/outreach/send`, `/outreach/suppress`, LinkedIn action
  endpoints, follow-up drafting, `/api/owner/outreach/bounces`, and `/api/owner/*` use HTTP
  Basic authentication. They return `503` when owner credentials are not configured and `401`
  for invalid credentials.
- Public/fetched strings are inert data. Unsupported profile claims are removed by the
  grounding verifier.

An attributed field has this shape:

```json
{
  "value": "Platform Engineer",
  "source_url": "https://public.example/profile",
  "confidence": "medium",
  "why": "role line observed in the public result title",
  "source_kind": "public_profile",
  "subject_name": "Sarah Chen",
  "company_level": false
}
```

`source_kind`, optional `subject_name`, and `company_level` distinguish person evidence from
published organisation contacts such as `security.txt` or public RDAP/WHOIS addresses.

Unknown enrichment fields are `null` or absent from their collection; they are never guessed.

## Candidate and dossier objects

A candidate preserves the v1 card keys (`id`, `name`, `headline`, `company`, `photo_url`,
`initials`, `source_link`, `source_label`, `confidence`, and `why`) and adds:

- `name_detail`, `role`, `company_detail`, `location`, `bio`, and `photo`: attributed fields
  or `null`.
- `avatar`: `{kind: "photo"|"gravatar"|"initials", url, initials, source_url}`. Initials
  remain available when a hotlinked image fails.
- `profiles[]`: exactly `{kind, url, handle, source_url, verified}`. Kinds can include
  `linkedin`, `github`, `x`, `instagram`, `personal_site`, `company_bio`, and `speaker`.
  A link is included only when it was observed on a public result/page and tied to the name.
- `submitted_name`: the visitor's search hint; `name` is the best public display name and is
  never replaced by a fabricated surname.
- `surname_resolved`: `true` only when public identity evidence establishes a surname. It is
  explicitly `false` for an unresolved single-token name. `name_detail.source_url` identifies
  the public result/profile/page that supplied `name`.
- `name_variants[]`: derived display-name alternatives used for bidirectional nickname handling
  and name-order-aware inference; the displayed `name` retains accents and punctuation.
- `email`: the primary address for backward compatibility, or `null`.
- `emails[]`: the complete ranked address set. Each item is
  `{address, status, confidence, source_url, why, pattern, score, mx_valid, source_kind,
  company_level}`. `status` is `verified` only for a publicly published address or a positive
  configured verification API result; a pattern-derived address is always `inferred`. MX alone
  never verifies a mailbox.

`CandidateDossier` contains `candidate_id`, `person`, `company`, and attributed public
`documents`. Public documents include observed HTTP links, link labels, and `email_addresses`
decoded from `mailto:` links. Person fields include headline, company, public profiles/email,
talks, and recent mentions. Company fields include domain/site, careers page, engineering blog,
GitHub org, observed email patterns, technology stack, news, funding, and feeds. Every fact
carries its own source URL and confidence reason.

`RoleMatch` is:

```json
{
  "title": "Backend Engineer",
  "team": "Platform",
  "location": "Dublin, Ireland",
  "canonical_apply_url": "https://boards.greenhouse.io/acme/jobs/4242",
  "requisition_id": "4242",
  "ats": "greenhouse",
  "fit_score": 88,
  "evidence": [
    {"signal": "skills", "evidence": "Shared evidenced terms: python", "source": "CV and public job description"}
  ],
  "source_url": "https://boards.greenhouse.io/acme"
}
```

Roles come only from an observed public ATS/careers page. Supported detectors are Greenhouse,
Lever, Ashby, Workable, SmartRecruiters, and Recruitee. An ordinary careers-page link parser is
the fallback. No role, requisition, or application URL is synthesized. A fallback role whose
page exposes no requisition has `requisition_id:null`; referral copy is omitted for it.

## Public and session endpoints

### Health, contact, and shell

| Method | Path | Result |
|---|---|---|
| `GET` | `/api/health` | Status, version, active answer provider/model, and `authority-gated` grounding label. |
| `GET` | `/api/contact` | Public email/location and, only with `TWIN_SHOW_PHONE=true`, phone. |
| `GET` | `/api/github` | Live metadata for the ten allow-listed repositories. |
| `GET` | `/`, `/embed` | Current frontend page. |
| `GET` | `/widget.js`, `/favicon.ico`, `/static/*` | Widget and static assets. |

### Session lifecycle

`POST /api/sessions` creates a session (`201`):

```json
{
  "session_id": "uuid",
  "greeting": "...",
  "name_optional": true,
  "research": {"type": "research", "status": "idle", "disclosure": "..."}
}
```

It queues a non-blocking `new_visitor_session` owner notification when configured.

`POST /api/sessions/{id}/identity` accepts:

```json
{"name": "Sarah Chen", "company": "Acme", "location": "Dublin"}
```

All fields are optional. A non-empty name returns `202` immediately and starts background
public research; chat is already usable. It queues `research_started`. An empty name takes the
same path as Skip. `POST /api/sessions/{id}/skip` explicitly skips and returns `200`.

The submitted name is a search hint, not an email-local-part input. Search titles, attributed
team/leadership pages, public GitHub profile names, and conference bios are ranked first. The
highest-confidence compatible public display name and its own `source_url` are recorded before
any address is inferred. Matching handles initials, fuzzy surname spelling, name-order changes,
hyphenated/double surnames, and a small bidirectional nickname table (for example,
Mike/Michael, Bob/Robert, and Sasha/Alexander). NFKD/diacritic folding is applied only while
forming local parts; display names are unchanged.

`GET /api/sessions/{id}/research` returns the last retained high-level research state. Progress
and derived events are transient SSE events and do not replace this value.

`GET /api/sessions/{id}/events` opens the SSE stream described below.

`POST /api/sessions/{id}/intent` accepts one of:

```json
{"intent": "hiring"}
```

Valid values are `hiring`, `networking`, and `exploring`.

`GET /api/sessions/{id}/dossier` returns `status`, enriched `candidates[]`, `dossiers[]`,
per-source reports, and an authority disclosure. This endpoint may be used for review cards
before confirmation; that does not grant model authority.

`POST /api/sessions/{id}/confirm` accepts `{"candidate_id":"..."}`. It returns the candidate,
dossier, ranked roles, and prepared outreach for that candidate. Confirmation persists only the
selected attributable context and changes the CRM stage to `confirmed`. A stale/unknown
candidate returns `404`.

`POST /api/sessions/{id}/research/opt-out` cancels work, purges candidates/dossiers/roles and
mutable drafts/proof packs, clears confirmed context, and suppresses discovered addresses from
future automation. The append-only record of an already attempted/sent effect is retained.

`DELETE /api/sessions/{id}` returns `204`, cancels work, clears ephemeral state, and deletes the
visit, messages, mutable drafts, and proof packs.

### Chat and fit

`POST /api/sessions/{id}/chat` accepts `{"message":"..."}` and returns:

```json
{
  "answer": "...",
  "sources": ["CV › Summary"],
  "grounded": true,
  "refusal": false,
  "tailored_for": null,
  "budget_remaining": 11234
}
```

Only confirmed visitor context can set `tailored_for`. Salary negotiation, offer acceptance,
contractual commitments, and start-date promises are always refused and handed to Prathamesh.

`POST /api/sessions/{id}/jd-fit` accepts `{"description":"..."}` and returns evidence coverage,
matched requirements with CV sources, unevidenced requirements, a summary, and caveat.

### Roles, company fit, handoff, and proof

| Method | Path | Result |
|---|---|---|
| `GET` | `/api/sessions/{id}/roles` | `by_candidate` map of ATS discovery/ranked `RoleMatch` results. |
| `GET` | `/api/sessions/{id}/company-fit` | Confirmed-only attributed company/profile overlap; `409` before confirmation. |
| `GET` | `/api/sessions/{id}/calendar` | Configured calendar URL/CTA or `configured:false`. |
| `POST` | `/api/sessions/{id}/proof-pack` | Confirmed-only expiring evidence pack URL (`201`). |
| `GET` | `/api/proof-packs/{token}` | Public share payload until expiry; otherwise `404`. |
| `POST` | `/api/sessions/{id}/recruiter/verify` | Compares `{"email":"..."}` with the attributed confirmed company domain. |

Corporate verification means domain equality/subdomain equality only. It does not claim that a
person works there beyond that observable address-domain check.

## Email discovery

Discovery is ordered and source-attributed:

1. Harvest explicitly published person addresses from attributed page text and `mailto:` links,
   personal/contact pages, team/about/leadership pages, press releases, speaker/CFP pages, the
   public GitHub profile API, and public commit author metadata from the user's repositories.
   `users.noreply.github.com` is discarded.
2. Check `/.well-known/security.txt`, `/security.txt`, and public domain RDAP/WHOIS data for
   organisation contacts. These remain `verified` because they are published, but are marked
   `company_level:true`.
3. Only when no published address exists, require a valid MX record and generate the complete
   name/domain set: `first.last`, `firstlast`, `first_last`, `flast`, `f.last`, `first`, `last`,
   `lastf`, `last.first`, `first-last`, `firstl`, initial-only forms, middle/middle-initial forms,
   compound-surname alternatives, name-order alternatives, and bidirectional nickname forms.
4. Rank each inferred item by a cached naming pattern observed from another attributed employee
   address on that domain, then general pattern prevalence. Every inferred item includes
   `pattern`, numeric `score`, `mx_valid:true`, and an explanatory `why`. A domain with no MX
   returns no inferred candidates.

`TWIN_EMAIL_VERIFICATION_PROVIDER=none` is the default. `hunter` plus
`TWIN_EMAIL_VERIFICATION_API_KEY` enables the pluggable HTTP verifier. When available, its
domain-search endpoint is consulted first for a known company pattern; a positive mailbox result
can promote a top inferred item to `verified`. Provider failure degrades to honest inference.
The application never performs SMTP `RCPT TO` probing.

## SendDecision and outreach

The current owner policy is count-based:

1. When `TWIN_FANOUT_UNSELECTED=true` and `1 <= candidate_count <= TWIN_FANOUT_MAX` (default
   max `3`), `decision` is `auto` and every candidate with a usable published or MX-enabled
   inferred address set is prepared for unattended delivery without selection.
2. A sole candidate at/above `TWIN_SEND_CONFIDENCE_THRESHOLD` (default `85`) uses
   `single_match`, which may say “You just talked to my digital twin.”
3. Multiple candidates, or a lower-confidence sole candidate, use `fanout`. It says they
   **may** have looked at the profile and that they can ignore the message if not. It never says
   the recipient visited.
4. More candidates than `TWIN_FANOUT_MAX`, or a disabled fanout flag, produce `review` and no
   unattended send.

Address selection then applies independently to each authorised person:

- Every distinct published/API-verified address is selected. If at least one exists, no inferred
  address is selected.
- Otherwise only the top `TWIN_INFERRED_SEND_MAX` inferred addresses are selected (default `3`;
  `0` disables inferred delivery). The cap is enforced when drafts/automatic work are created,
  not merely in the response.
- Dedupe is case-insensitive. Dots and `+tag` aliases are collapsed only for Gmail/Googlemail,
  where those are provider rules; punctuation remains significant on every other domain.
- A recorded bounce suppresses the normalised address and subtracts from that pattern's future
  score for the domain. Persistent bounce counts survive process restarts.

Policy authorization is not sufficient for transport. Every SMTP attempt still requires
`TWIN_AUTOSEND=true`, complete Gmail SMTP settings, a non-suppressed recipient, global
`TWIN_DAILY_SEND_CAP` capacity, per-person/once-only capacity, and passing sender-domain
SPF/DKIM/DMARC preflight. Multiple addresses selected for one authorised person are one campaign
for the per-person policy, while each actual message still consumes the global daily cap. A
later campaign cannot bypass once-only protection by discovering another address or profile URL.
A missing address is a recorded refusal. CI forces all automation off.

`GET /api/sessions/{id}/outreach` returns the `SendDecision`, prepared drafts, and whether SMTP
automation is configured. Each draft has 1–3 exact variants, its recipient confidence, LinkedIn
drafts, template kind, and timestamps. Recipient fields are `recipient_status`,
`recipient_pattern`, `recipient_score`, `recipient_why`, `recipient_source_url`,
`recipient_source_kind`, and `recipient_company_level`.

The owner review flow is:

1. `POST /outreach/approve` (Basic auth) with `{"draft_id":"...","variant_id":"warm"}`.
   The result includes an expiring token bound to draft ID, normalized recipient, variant ID,
   and SHA-256 of the exact stored body.
2. `POST /outreach/send` (Basic auth) with the same fields plus `approval_token`.
   A valid token can deliver a reviewed draft. If SMTP automation is off, it returns
   `status:"compose"` and a `mailto_url`; it does not send.

Send results use `sent`, `compose`, `duplicate`, `suppressed`, `refused`, or `capped`, plus
`transport`, `reason`, and optional DNS `preflight`. Each decision/reservation/result is appended
to the audit with the attempted address's status, pattern, score, source URL/type, company-level
flag, rationale, `decision`, `reason`, `template`, and variant metadata.

`POST /outreach/suppress` (Basic auth) accepts `{"address":"...","reason":"..."}`.
`GET /outreach/opt-out?token=...` is the signed link placed in every email; it immediately and
idempotently suppresses the address.

`POST /api/owner/outreach/bounces` (Basic auth) accepts:

```json
{
  "address": "sarah.chen@acme.io",
  "pattern": "first.last",
  "reason": "provider hard bounce",
  "session_id": "optional-session-id",
  "candidate_id": "optional-candidate-id"
}
```

If `pattern` is omitted, the endpoint reuses the latest audit metadata for that normalised
address when available. It immediately adds the address to suppression, increments the durable
domain/pattern bounce count, updates in-process ranking, and appends `email.bounced` to the audit.
The response returns `status:"bounced"`, `suppressed:true`, `domain`, `pattern`, and
`pattern_bounce_count`.

`POST /api/sessions/{id}/outreach/follow-up` (Basic auth) accepts a parent `draft_id`. It only
creates a review draft after the configured delay and after the initial message is logged sent;
it never auto-sends the follow-up. Early calls return `409` with the availability time.

## LinkedIn owner automation

Playwright is an optional install:

```bash
pip install -e ".[linkedin]"
playwright install chromium
```

The browser uses only the owner’s local, gitignored `TWIN_LINKEDIN_USER_DATA_DIR`. The API never
accepts or stores LinkedIn cookies/passwords. Only a LinkedIn URL actually observed in that
candidate’s public profiles is actionable.

1. `POST /api/sessions/{id}/linkedin/approve` (Basic auth) with `candidate_id`, observed
   `profile_url`, `action` (`follow`, `connect`, or `message`), and optional `message`. It returns
   an exact action-bound approval token.
2. `POST /api/sessions/{id}/linkedin/action` adds `approval_token` and normally
   `automatic:false`. `automatic:true` is accepted only with `TWIN_LINKEDIN_AUTO=true`.

The result is `completed`, `unavailable`, `duplicate`, `capped`, `killed`, `challenge`, or
`refused`. Actions have randomized human-scale delays, a conservative daily cap, and once-only
keys. `TWIN_LINKEDIN_KILL_SWITCH=true` blocks all actions. A challenge/CAPTCHA/security check
stops immediately with `handoff_required:true`; the implementation does not use stealth,
proxies, CAPTCHA bypasses, or alternate accounts.

## Owner endpoints

| Method | Path | Result |
|---|---|---|
| `GET` | `/owner` | Existing owner HTML. |
| `GET` | `/api/owner/visits` | Visit summary, questions, CRM stage/intent, and in-process send decision. |
| `GET` | `/api/owner/export.csv` | Visit CSV export. |
| `GET` | `/api/owner/contacts` | CRM contacts: `visited → confirmed → drafted → sent → replied`. |
| `POST` | `/api/owner/contacts/{id}/stage` | Sets one valid CRM stage, including owner-recorded `replied`. |
| `GET` | `/api/owner/sessions/{id}/replay` | Contact timeline, messages/sources, and available research. |
| `GET` | `/api/owner/outreach` | Append-only actions, decision/address metadata, sent/compose/failed/bounced variant counts, and `tracking_pixels:false`. |
| `POST` | `/api/owner/outreach/bounces` | Suppresses a bounced address, learns its domain pattern penalty, and appends `email.bounced`. |

## SSE contract

The endpoint is `GET /api/sessions/{id}/events`. Each frame is:

```text
event: research.progress
data: {"type":"research.progress","source":"careers","status":"ok","message":"Careers checked"}

```

On connect, the retained research state is sent immediately. A `: keep-alive` comment is sent
after 15 seconds without an event. Event types are:

| SSE event / `type` | Fields and meaning |
|---|---|
| `research` | `status` is `idle`, `researching`, `candidates`, `empty`, `confirmed`, `skipped`, or `opted_out`. Completed events include candidates, dossiers, source reports, message, and disclosure. |
| `research.progress` | `source`, per-source `status` (`running`, `ok`, `empty`, `blocked`, `timeout`, or `failed`), and short `message`. Transient. |
| `research.dossier` | Enriched `candidates[]` and attributed `dossiers[]`. Transient; still not model authority. |
| `roles.ready` | `roles` map keyed by candidate ID, each containing ATS status/reason and `roles[]`. |
| `outreach.ready` | Computed `decision` and prepared card `drafts[]`. |
| `outreach.action` | Candidate (for automatic effects) plus send `status`, `transport`, `reason`, optional `mailto_url`/`preflight`. |
| `intent` | Recorded visitor `intent` and `status:"recorded"`. |
| `handoff` | Confirmed candidate ID, `status:"ready"`, optional `calendar_url`, and message. |
| `company_fit.ready` | Fit score, attributed signals, summary, and caveat. |
| `proof_pack.ready` | Share `url` and `expires_at`. |
| `linkedin.action` | LinkedIn action result, detail, and `handoff_required`. |

Transient events are broadcast to connected clients but do not replace the value returned by
`GET /research`; this preserves compatibility with the current page’s polling behavior.

## Gmail SMTP and notifications

Real SMTP is restricted to `smtp.gmail.com:587` with STARTTLS. The manual connectivity test
negotiates TLS and authenticates but has no send operation:

```bash
digital-twin-smtp-check
# or: python -m digital_twin.smtp_check
```

It prints only status/host/port and a safe diagnostic; it never prints the password or account
identifier and never sends mail.

When enabled, Pushover posts form data to
`https://api.pushover.net/1/messages.json` for session start, research start/completion, email
sent/refused, LinkedIn action, and errors. Messages include only short known context. Pushover,
webhook, and Telegram adapters are rate-limited, background-only, mockable, and non-fatal.

Runtime settings use the `TWIN_` prefix; real secret values stay in ignored `.env`. No test or CI
path performs SMTP, LinkedIn, public research, or notification network effects.
