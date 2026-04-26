# Creative Loop, Site Hardening, and Staged Rollout Implementation Plan

> **For Vesper:** Use this as the execution plan. Do not dump everything into production at once. Build, verify, then feed tasks into Task Board slowly enough that Cassie can actually watch the system behave.

**Goal:** Fix the `cassiopeia.jp.net` deployment issues, improve the site's voice and link integrity, formalize the creative review loop inside Task Board, and simplify/rename misleading agents so the board reflects the system that actually exists.

**Architecture:** This work spans two repos. `cassie-dev` gets deployment and content hardening. `task-board` gets a proper editorial workflow layer with artifact-aware review, clearer role semantics, and a staged rollout plan. Cassie is the **final approver / escalation point**, not a routine intermediate reviewer.

**Tech Stack:** Hugo, GitHub Pages, custom domain via Cloudflare, Flask, SQLAlchemy, existing Task Board state machine, existing handoff/review endpoints.

---

## Current findings to anchor the work

### Site findings
- `cassie-dev/hugo.yaml` currently uses:
  - `baseURL: https://acgh213.github.io/cassie-dev/`
- `themes/cassie/layouts/partials/head.html` uses:
  - `{{ "css/style.css" | relURL }}`
- This is correct for the GitHub Pages subpath, but wrong for a custom root domain.
- There is currently **no `CNAME` file** in the repo.
- On custom domain, the white background symptom strongly suggests CSS is still being requested under the wrong path.

### Content findings
- Homepage project cards are a mix of:
  - external GitHub links
  - internal `/projects/...` links
  - one plain non-link (`GameVault`)
- Project detail pages exist for:
  - Hermes Vesper
  - Vesper Blog
  - Quiet Site
  - Memory Atlas
  - Cost Atlas
  - Observatory
  - Agent Comms
- Copy quality is uneven:
  - some pages sound like Cassie
  - some sound like an intelligent brochure trying not to get fired
- Hermes Vesper page still mentions Codex and Claude Code explicitly in credits.

### Task Board findings
- Review handoff/status bug was partially fixed already.
- System enforces no self-review.
- Review still acts like generic task approval instead of editorial production.
- Creative tasks do not yet make artifacts first-class.
- Agent names were historically `codex` and `claude-code`, which implied external systems rather than the actual operational substrate. Canonical runtime ids should remain role-based.

---

# Part I — cassie-dev site hardening

## Task 1: Fix custom-domain pathing

**Objective:** Make CSS and internal links work correctly on the custom root domain.

**Files:**
- Modify: `/home/exedev/cassie-dev/hugo.yaml`
- Create: `/home/exedev/cassie-dev/static/CNAME`
- Verify: `/home/exedev/cassie-dev/public/index.html`

**Step 1: Update Hugo baseURL for the real domain**

Change:

```yaml
baseURL: https://acgh213.github.io/cassie-dev/
```

To:

```yaml
baseURL: https://cassiopeia.jp.net/
```

**Step 2: Add CNAME file**

Create:

```text
cassiopeia.jp.net
```

at:

```text
/home/exedev/cassie-dev/static/CNAME
```

**Step 3: Build and verify generated paths**

Run:

```bash
cd /home/exedev/cassie-dev && hugo --minify
```

Expected in `public/index.html`:

```html
<link rel="stylesheet" href="/css/style.css">
```

not `/cassie-dev/css/style.css`.

**Step 4: Verify output files**

Check:
- `public/CNAME` exists
- `public/css/style.css` exists

**Step 5: Commit**

```bash
git add hugo.yaml static/CNAME public/
git commit -m "fix: support custom domain root deployment"
```

---

## Task 2: Audit and repair homepage/project links

**Objective:** Make every homepage project card and project page link somewhere intentional.

**Files:**
- Modify: `/home/exedev/cassie-dev/content/_index.md`
- Modify as needed: `/home/exedev/cassie-dev/content/projects/*.md`

**Rules:**
- Homepage cards should usually link to the **internal project page first**
- Each project page should then offer:
  - live link (if available)
  - GitHub link (if available)
- If a project does not publicly exist yet, say so plainly instead of fake-linking it

**Specific repairs to make:**
1. `Hermes Vesper` homepage card should link to `/projects/hermes-vesper/`, not directly to GitHub
2. `Memory Atlas`, `Cost Atlas`, `Observatory`, `Agent Comms` should keep internal project links
3. `Vesper Blog` and `Quiet Site` can still have internal detail pages as primary links, with live links inside
4. `GameVault` needs an intentional state:
   - either add a real project page
   - or mark it as private / local / not public yet

**Verification command:**

```bash
cd /home/exedev/cassie-dev && hugo --minify
```

Then manually inspect:
- homepage card targets
- project detail page outbound links
- no dead `#` placeholders unless explicitly labeled as unavailable

**Commit:**

```bash
git add content/
git commit -m "fix: normalize project link targets and public availability"
```

---

## Task 3: Rewrite weak project copy in Cassie's voice

**Objective:** Replace brochure copy with first-person, lived, slightly wry project descriptions.

**Files:**
- Modify: `/home/exedev/cassie-dev/content/_index.md`
- Modify: `/home/exedev/cassie-dev/content/projects/hermes-vesper.md`
- Modify: `/home/exedev/cassie-dev/content/projects/*.md`

**Copy standard:**
- First person when appropriate
- More concrete operational details
- Less “this is an innovative platform” energy
- Let Cassie sound like Cassie, not a startup intern in a blazer

**Priority pages:**
1. Homepage blurbs
2. Hermes Vesper
3. Memory Atlas
4. Cost Atlas
5. Observatory
6. Agent Comms

**Verification:**
- Read homepage aloud
- If a sentence could survive on LinkedIn without embarrassment, it probably still needs work

**Commit:**

```bash
git add content/
git commit -m "feat: rewrite site copy in Cassie's voice"
```

---

## Task 4: Verify both deployment surfaces

**Objective:** Confirm the site works on both the custom domain and the GitHub Pages origin.

**Verification URLs:**
- `https://cassiopeia.jp.net/`
- `https://acgh213.github.io/cassie-dev/`

**Checks:**
- CSS loads on custom domain
- internal links resolve correctly on custom domain
- project pages render with theme
- outbound live/GitHub links work

**Note:** If supporting both domains gracefully becomes annoying, the custom domain wins. The origin URL can be tolerated as secondary.

---

# Part II — Task Board creative review loop

## Task 5: Add a dedicated creative workflow template definition

**Objective:** Stop pretending generic tasks are enough for editorial work.

**Files:**
- Modify: `/home/exedev/task-board/docs/vision.md`
- Modify existing template seed file(s) if present
- Possibly modify: `/home/exedev/task-board/api.py`
- Possibly modify: `/home/exedev/task-board/models.py`

**Creative workflow stages:**
1. research
2. draft
3. edit
4. review
5. revise or approve
6. optional human approval

**Role semantics:**
- `researcher`: produces source packet / notes
- `essayist`: produces draft from approved brief/research
- `editor`: edits text, preserves voice, improves structure
- `reviewer`: judges readiness, does not rewrite the draft directly
- `cassie`: final approver / escalation point

**Deliverable:**
A written workflow contract in docs and, if templates exist in DB, a seed/update path for a `creative-editorial` template.

---

## Task 6: Make creative artifacts first-class

**Objective:** Review should attach to real files, not just vibes and status changes.

**Files:**
- Modify: `/home/exedev/task-board/models.py`
- Modify: `/home/exedev/task-board/api.py`
- Modify: `/home/exedev/task-board/templates/task.html`
- Create tests under `/home/exedev/task-board/tests/`

**Add fields or equivalent linked structure for:**
- `research_artifact_path`
- `draft_artifact_path`
- `edited_artifact_path`
- `review_artifact_path`
- `revision_brief_path`

If adding columns is too messy for first pass, add a single JSON/text field such as:

```python
artifacts = db.Column(db.Text, default='{}')
```

with normalized keys.

**Minimal JSON shape:**

```json
{
  "research": "/path/to/research.md",
  "draft": "/path/to/draft.md",
  "edited": "/path/to/edited.md",
  "review": "/path/to/review.md",
  "revision_brief": "/path/to/revision.md"
}
```

**Tests to add:**
- task can store artifact paths
- review endpoint preserves artifacts
- task detail API returns artifacts

---

## Task 7: Add structured creative review data

**Objective:** Replace mushy freeform review-only behavior with editorial structure.

**Files:**
- Modify: `/home/exedev/task-board/models.py`
- Modify: `/home/exedev/task-board/api.py`
- Modify: `/home/exedev/task-board/templates/task.html`
- Tests: `/home/exedev/task-board/tests/test_api.py` or new `test_creative_review.py`

**Extend review payload to support:**

```json
{
  "reviewer": "reviewer",
  "decision": "request_changes",
  "feedback": "High-level summary",
  "review_type": "creative",
  "strengths": ["..."],
  "structural_issues": ["..."],
  "voice_issues": ["..."],
  "required_changes": ["..."],
  "optional_polish": ["..."],
  "recommendation": "revise"
}
```

**Behavior:**
- Generic tasks can still use plain review
- Creative tasks should accept and return the structured fields

**Verification:**
- Submit creative task review
- Confirm task detail page shows review sections clearly
- Confirm `request_changes` stores an actionable editorial brief

---

## Task 8: Turn `request_changes` into a real revision loop

**Objective:** Requested changes must become the next brief, not just a sad event log entry.

**Files:**
- Modify: `/home/exedev/task-board/api.py`
- Modify: `/home/exedev/task-board/models.py`
- Modify: `/home/exedev/task-board/templates/task.html`
- Tests: new revision-loop tests

**Required behavior:**
- On creative `request_changes`, preserve the prior review in a revision brief artifact or structured field
- Reassign task back to `editor` or `essayist` depending on configured stage
- Keep a visible revision count
- Keep previous review accessible from task detail

**Minimal acceptable behavior for v1:**
- Store structured review
- set `status = needs_revision`
- attach revision brief
- assign back to named next agent

---

## Task 9: Add Cassie final-approval semantics

**Objective:** Model Cassie as final approver / escalation point, not an always-on reviewer.

**Files:**
- Modify: `/home/exedev/task-board/models.py`
- Modify: `/home/exedev/task-board/api.py`
- Modify: `/home/exedev/task-board/docs/vision.md`
- Tests: new creative approval tests

**Rules:**
- Creative tasks can pass agent review without immediately becoming public/final
- Add a tag or task flag such as `requires_final_approval`
- When set, successful reviewer approval should move the task to `needs_human` instead of terminal completion
- Cassie then approves or sends back changes

**Important:**
Cassie should only appear where it matters:
- publishable essay
- public copy
- something with voice/taste implications

Not every tiny subtask needs a divine audience.

---

# Part III — Agent cleanup and naming

## Task 10: Remove or rename misleading provider-branded agents

**Objective:** Stop implying Claude Code/Codex subprocesses if the system is just using models directly.

**Files:**
- Modify via API/UI: Task Board agent records
- Modify docs/pages mentioning these names:
  - `/home/exedev/cassie-dev/content/projects/hermes-vesper.md`
  - `/home/exedev/task-board/docs/vision.md`
  - any seed scripts such as `/home/exedev/task-board/register_clones.py`

**Recommended rename direction:**
- `claude-code` -> `frontend-specialist` or `ui-specialist`
- `codex` -> `backend-specialist` or `systems-specialist`

**Why rename instead of delete immediately:**
- preserves continuity/history
- avoids breaking references everywhere at once
- makes the system tell the truth about what those agents actually do

**Data model caution:**
Because `Agent.name` is the primary key, rename is not just a label tweak if historical references matter.

**Safe v1 approach:**
- keep stable internal names if needed for now
- update `display_name`, `role`, `skills`, and public copy first
- later decide whether a real migration of agent names is worth it

---

## Task 11: Update public/project copy to match the new agent naming truthfully

**Objective:** Remove misleading “built with Codex and Claude Code” framing.

**Files:**
- Modify: `/home/exedev/cassie-dev/content/projects/hermes-vesper.md`
- Modify any other public references

**Example direction:**
Replace:

```markdown
Built with Vesper, Codex, Claude Code...
```

with something like:

```markdown
Built with Vesper and a rotating cast of specialized agents for research, editing, backend work, and UI cleanup.
```

That is both truer and less cosplay.

---

# Part IV — Slow staged rollout through Task Board

## Task 12: Create rollout tasks on the board in deliberate order

**Objective:** Feed the system tasks slowly enough to watch behavior instead of flooding it.

**Task order to create:**
1. Fix custom-domain CSS / baseURL / CNAME
2. Normalize homepage project links
3. Rewrite homepage/project blurbs
4. Update Hermes Vesper page credits / agent references
5. Define creative editorial template
6. Add artifact support
7. Add structured creative review
8. Add revision brief loop
9. Add Cassie final approval semantics
10. Update agents / display names / skills
11. Run smoke test of creative loop
12. Run one real trickle workflow through the board

**Assignment strategy:**
- First 3 site tasks: one at a time
- Then 1 workflow task at a time
- No mass auto-assign burst
- Watch the board, audit trail, and handoffs after each completion

**Suggested initial agent mapping:**
- site deployment/pathing -> backend/systems specialist
- site copy rewrites -> editor or essayist
- workflow model changes -> backend/systems specialist
- task detail UI changes -> ui specialist
- smoke test + verification -> reviewer / QA

---

## Task 13: Run one real creative task through the repaired loop

**Objective:** Prove the editorial workflow with a single controlled example.

**Scenario:**
- Create one creative task with:
  - research artifact
  - draft artifact
  - editor pass
  - structured reviewer feedback
  - revision request
  - final reviewer approval
  - optional Cassie approval if flagged

**Success criteria:**
- No self-review
- Review artifacts visible
- Revision brief survives into next round
- Task history is legible from the board
- Cassie only appears at the final approval/escalation layer

---

# Definition of done

This plan is complete when:
- `cassiopeia.jp.net` loads styled correctly
- homepage/project links all go somewhere intentional
- weak copy has been rewritten in Cassie's voice
- misleading provider-branded agent language is cleaned up
- creative tasks can carry real artifacts
- reviewer feedback is structured
- `request_changes` produces a genuine revision loop
- Cassie is modeled as final approver/escalation point
- rollout happens gradually through Task Board so the process is visible, not theatrical

---

# Recommended next execution move

1. Do the **site pathing/domain fix first** because the white background is ugly and immediate.
2. Then do the **homepage/project link + copy audit**.
3. Then begin the **creative workflow build** inside Task Board.
4. Only after that, start the **slow trickle rollout** through real board tasks.
