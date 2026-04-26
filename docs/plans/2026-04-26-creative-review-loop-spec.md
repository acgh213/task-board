# Creative review loop as Task Board artifacts

## Goal
Make the editorial pipeline explicit without forcing Cassie into routine intermediate review. Cassie is the final approver / escalation point.

## Core artifacts
1. **Research task**
   - output: notes, references, tensions, examples
   - done when a writer can draft from it without re-researching basics
2. **Draft task**
   - output: full draft, not outline fragments
   - assigned to writer / essayist role
3. **Creative edit task**
   - output: sharpened draft with line edits, structure notes, and unresolved questions
   - assigned to editor-creative
4. **Final review task**
   - output: approve / request changes / escalate to Cassie
   - assigned to reviewer
5. **Cassie approval task** (only when needed)
   - status target: needs_human or needs_vesper equivalent for final signoff
   - used for publication approval, not routine style pass-through

## Task fields that matter
- `project`: collection or surface (`vesper-blog`, `constructed-selves`, etc.)
- `tags`: include stage tags such as `stage:research`, `stage:draft`, `stage:creative-edit`, `stage:final-review`, `stage:approval`
- `reserved_for`: optional for role-constrained work when a specific specialist should take it
- `blocked_by`: explicit dependency chain between stages
- `result`: canonical handoff payload; should contain the actual draft/review text, not just 'done'
- `last_error` / `failure_reason`: use for malformed handoffs or bogus review states

## State rules
- Research / draft / edit tasks can move through `assigned -> claimed -> in_progress -> submitted -> in_review`.
- Reviewer can:
  - `approve` → `completed`
  - `request_changes` → `needs_revision`
  - `reject` → `failed`
- Cassie is not auto-inserted as a reviewer on every pass.
- Cassie only appears via escalation or final approval tasks.

## Handoff rules
- A handoff should either:
  - preserve one task while changing owner with a validated stage label, or
  - create a new downstream task and complete the upstream one.
- Do **not** leave orphaned `in_review` or `assigned` tasks after a handoff is accepted.
- Reviewer tasks must contain an actual review payload, not placeholder text like 'Reviewed and approved.'

## UI implications
- Active column should show genuinely live work.
- Completed work belongs in a collapsible archive so the board does not become a mausoleum.
- Task cards should render agent **display names**, not internal slugs, so role cleanup is visible immediately.

## Immediate follow-ups
1. Resolve stale active tasks left behind by earlier handoff experiments.
2. Keep canonical role-based agent ids in place (`systems-specialist`, `interface-specialist`) and avoid reintroducing branded slugs.
3. Keep the next rollout task visible but singular: assign one follow-up task at a time.