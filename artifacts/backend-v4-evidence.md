# Backend v2 + v3 + v4 verification evidence

Verified locally on 2026-08-02. No real credential, recipient content, cookie, or browser
profile is stored in this artifact.

## Quality gate

```text
python -m ruff format --check .
39 files already formatted

python -m ruff check .
All checks passed!

python -m pytest -q
.............................................................            [100%]
61 passed in 3.98s
```

The suite runs with pytest socket blocking and explicit CI automation-off variables. It covers:

- pre-confirmation structural exclusion of candidate and dossier data from model context;
- attributed enrichment, no invented profile links, photo/Gravatar/initials fallback;
- robots exclusion, per-source timeouts, cancellation, poisoned-content removal;
- public HN Algolia, GitHub organisation activity, and RSS/Atom adapters with mock transports;
- published/inferred email labels, domain patterns, syntax and MX checks;
- all six ATS detectors, careers fallback, real requisition URLs, and role ranking;
- count-based small-set fanout and safe non-visitor-claiming copy;
- exact-body approval tokens, suppression, opt-out, once-only and daily send caps;
- SPF/DKIM/DMARC refusal before transport;
- LinkedIn per-action confirmation, daily cap, once-only, kill switch, and challenge handoff;
- mock Pushover payloads, non-fatal failure, and burst limiting.

## Runtime smoke

A real Uvicorn process was started on `127.0.0.1:8765` with every outward automation flag
forced off for the process. Health, session creation, and grounded scripted chat completed:

```text
process-smoke=ok health=ok provider=scripted grounded=True sources=2
```

The generated OpenAPI schema contains 35 application paths. The complete endpoint and SSE
contract is in `docs/API.md`.

## Delivery boundary

Live DNS inspection of the Gmail sender domain through the same application preflight returned:

```text
domain=gmail.com spf=True dkim=True dmarc=True selector=20230601 ready=True
```

The operator SMTP self-test used the ignored `.env`, negotiated STARTTLS on
`smtp.gmail.com:587`, and authenticated successfully. The command has no send operation and no
message was sent:

```text
ok=True host=smtp.gmail.com port=587 starttls=True authenticated_as=null
```

`git check-ignore` confirmed `.env` is ignored, `git ls-files` confirmed it is untracked, and an
in-memory comparison of configured sensitive values against the complete Git history returned:

```text
env-ignore=verified credential-history-scan=clean sensitive-values-checked=4
```

The concurrent frontend-owned files under `src/agentic_digital_twin/static/` were not staged or edited
as part of this backend work.
