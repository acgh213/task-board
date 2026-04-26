# Phase 4 Specification — Dashboard v2 + Templates + Agent Expansion

## Overview

Make the dashboard actually useful for monitoring, add task templates for common work patterns, and expand the agent roster so tasks actually get routed to different specialists.

---

## 1. Dashboard v2

### Current problems:
- Kanban is static — no auto-refresh
- No way to see task timeline (what happened when)
- No way to filter by project/agent/priority
- Agent cards are just names — no history

### New features:
- **Auto-refresh** every 30s via meta refresh or JS fetch
- **Task detail modal** — click a task to see full timeline, reviews, events
- **Filters** — by status, agent, project, priority
- **Agent history** — click an agent to see their completed/failed tasks
- **Event stream** — live feed of what's happening

### Routes:
- `GET /` — main dashboard with filters
- `GET /agent/<name>` — agent detail page
- `GET /task/<id>` — task detail page (already exists, enhance it)

---

## 2. Task Templates

Pre-defined task patterns that create multi-step workflows.

### Templates:

**Feature Build:**
1. Research requirements (researcher)
2. Plan implementation (planner)
3. Write code (coder)
4. Write tests (coder)
5. Code review (editor)
6. Documentation (writer)

**Bug Fix:**
1. Investigate issue (researcher)
2. Write failing test (coder)
3. Fix code (coder)
4. Verify fix (reviewer)

**Documentation:**
1. Research topic (researcher)
2. Write draft (writer)
3. Review (editor)

**Infrastructure:**
1. Plan changes (planner)
2. Implement (coder)
3. Test (reviewer)
4. Deploy (devops — needs human approval)

### API:
- `GET /api/templates` — list templates
- `POST /api/templates/<name>/create` — create tasks from template

---

## 3. Agent Expansion

### Current agents:
- coder ✅ (actively used)
- editor ✅ (used for review)
- researcher ✅ (used for research)
- planner ✅ (used for planning)
- mission-control ✅ (routing)
- overseer ✅ (monitoring)
- reviewer ✅ (quality checks)

### New agents to create:
- **writer** — documentation, blog posts, READMEs
- **devops** — deployment, infrastructure, CI/CD
- **qa** — testing, validation, edge cases

### Skills to add to existing agents:
- coder: add `devops`, `ci-cd`
- editor: add `style`, `accessibility`

---

## 4. Template System Implementation

### Template model:
```python
class TaskTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    steps = db.Column(db.Text)  # JSON array of step definitions
    created_at = db.Column(db.DateTime)
```

### Step definition (JSON):
```json
[
    {
        "title": "Research {topic}",
        "description": "Research and document findings",
        "tags": "research",
        "agent": "researcher",
        "priority": 2
    },
    {
        "title": "Implement {topic}",
        "description": "Build the feature",
        "tags": "python,flask",
        "agent": "coder",
        "priority": 1,
        "depends_on": 0
    }
]
```

### Template execution:
- `POST /api/templates/<name>/create` with `{"variables": {"topic": "user auth"}}`
- Creates all tasks, sets up dependencies
- Auto-assigns based on agent tags

---

## Tests

### Dashboard tests:
- Dashboard renders with filters
- Agent detail page shows history
- Task detail shows timeline

### Template tests:
- List templates
- Create tasks from template
- Variable substitution
- Dependency ordering

### Agent tests:
- New agents have correct skills
- Auto-assign routes to correct specialist
- Template tasks go to right agents
