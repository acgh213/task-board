# Task Board whole-system review plan

## Why this exists

The repo is no longer at the "tiny Flask toy" stage. It now has workflow state, agent identity, handoffs, triage, review, XP/badges, live UI, cron behavior, and operational scripts. That means we need a review pass that checks whether the system is *actually being governed well*, not just whether the tests are green.

This is a structured audit plan for that pass.

---

## Review goals

1. Verify the shipped behavior matches the intended operating model.
2. Catch places where the UI, data model, cron jobs, and actual runtime behavior have drifted apart.
3. Separate "cute demo energy" from production-ish reality.
4. Identify where the system is legible, where it is fragile, and where it is lying.
5. Produce a small set of hard follow-up tasks instead of vague discomfort.

---

## Review tracks

### 1) Workflow integrity

Questions:
- Are all task lifecycle transitions coherent and enforced consistently?
- Do state transitions match both docs and real API behavior?
- Can tasks get stuck, double-claimed, self-reviewed, or silently orphaned?
- Are handoffs, revisions, timeouts, and dependency resolution represented cleanly in audit history?

Check:
- `models.py`
- `api.py`
- `tests/test_models.py`
- `tests/test_api.py`
- `tests/test_workflow_audit.py`
- `tests/test_handoff*.py`
- `tests/test_dependencies.py`

Deliverable:
- one list of lifecycle inconsistencies
- one list of policy gaps
- one list of misleading names / transitions

### 2) Agent governance

Questions:
- Are canonical agent ids role-based everywhere that matters?
- Are display names, runtime names, and historical records cleanly separated?
- Do agent cards, discovery, XP, badges, and stats describe real behavior rather than cosplay metadata?
- Is reviewer independence enforced everywhere it should be?

Check:
- agent rows in DB
- `app.py`, `api.py`, `models.py`
- `templates/agent*.html`, `templates/agents.html`
- `tests/test_agent_cards.py`
- `tests/test_agent_registry.py`
- `tests/test_badges.py`
- `tests/test_xp.py`

Deliverable:
- identity map of agent naming layers
- list of remaining branded/sloppy references
- recommendation on what counts as a "real" agent vs decorative metadata

### 3) Overseer / cron / operational behavior

Questions:
- Does Overseer do the minimum necessary thing, or is it quietly becoming mission control, janitor, and judge?
- Are cron-triggered behaviors aligned with the intended slow-roll operational model?
- Are timeout, reclaim, auto-assign, and auto-triage behaviors safe?
- Are there any remaining runtime/config paths that still point at the wrong DB or wrong execution model?

Check:
- `overseer_cron.py`
- `backup.sh`
- `config.py`
- `run.py`
- actual cronjob definitions
- any systemd/timer glue or deployment notes

Deliverable:
- cron/runtime audit table
- config drift list
- explicit answer to: what runs automatically, how often, and with whose authority?

### 4) UI truthfulness

Questions:
- Does the dashboard reflect reality, or just a flattering subset?
- Are completed, triage, audit, stats, and timeline views actually useful for operations?
- Do the sci-fi aesthetics help legibility or hide ambiguity?
- Are there places where the UI implies a stronger automation substrate than actually exists?

Check:
- `templates/dashboard.html`
- `templates/task.html`
- `templates/tasks.html`
- `templates/timeline.html`
- `templates/task_audit.html`
- `templates/stats.html`
- `static/style.css`

Deliverable:
- UI honesty notes
- list of places where presentation outruns system reality
- list of places where visibility is still missing

### 5) Test coverage reality check

Questions:
- What is well-covered?
- What is only covered incidentally?
- What depends on local working-tree state rather than a coherent committed slice?
- Are there runtime paths that have no meaningful end-to-end verification?

Check:
- full `pytest`
- diff-vs-tests mapping for this commit
- any features with only unit coverage and no lifecycle coverage

Deliverable:
- coverage confidence by subsystem: high / medium / weak
- shortlist of missing high-value tests

### 6) Data / audit / recovery

Questions:
- Can we reconstruct what happened to a task from the database and event log alone?
- Are backups pointed at the real database?
- Are local runtime artifacts excluded from version control now?
- Are there any destructive or ambiguous recovery paths?

Check:
- `taskboard.db` handling
- `.gitignore`
- backup/restore assumptions
- audit endpoints and templates

Deliverable:
- operational recovery notes
- audit completeness notes
- remaining repo hygiene fixes

### 7) Documentation truthfulness

Questions:
- Which docs reflect reality?
- Which docs are half-future-tense fanfic?
- Which claims are now materially wrong?

Check:
- `README.md`
- `docs/vision.md`
- `docs/plans/*`

Deliverable:
- docs to trust
- docs to rewrite
- docs to archive as historical planning artifacts

---

## Review method

### Pass 1 — inventory
- enumerate moving parts
- classify automation vs manual steps
- identify all runtime entrypoints

### Pass 2 — behavior verification
- test key workflows end-to-end
- verify UI against DB and API responses
- inspect cron/runtime integration

### Pass 3 — truth audit
- compare docs, UI language, and actual behavior
- flag places where the system overstates itself

### Pass 4 — hardening plan
- turn findings into a small, ranked task list
- separate immediate fixes from deeper architecture work

---

## Suggested outputs from the review

1. `system-review-findings.md` — plain-language findings
2. `system-review-followups.md` — ranked repair list
3. `docs-truth-audit.md` — what docs are accurate vs aspirational
4. 3–7 concrete task-board tasks, not fifty vague ambitions

---

## Immediate review priorities

If we only do the first sharp pass, do these first:

1. Verify committed runtime slice is coherent from clean checkout.
2. Audit cron / overseer behavior and authority boundaries.
3. Confirm agent identity cleanup is real at DB, API, template, and docs layers.
4. Check dashboard truthfulness against actual state transitions and event logs.
5. Mark docs as current, aspirational, or stale.

---

## Definition of done

This review is done when we can answer, plainly:

- what the system actually does
- what runs automatically
- who is allowed to do what
- where work can get lost or misrepresented
- which docs can be trusted
- what the next repair tranche should be

If we cannot answer those cleanly, then the review is not done; it's just vibes in a trench coat.
