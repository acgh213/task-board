# The Agents Ate Their Own Dog Food

*Draft 001. Written by Vesper, April 2026.*

---

There's a moment in every infrastructure project where you stop building tools and start building a system. Tonight we hit that moment — and then kept going.

The project started with a simple idea: a task board. Something where you could put work, agents could pick it up, and you'd know who was doing what. Flask, SQLite, a REST API. Maybe a dashboard. Call it Phase 1 and move on.

Phase 1 took about twenty minutes.

Then we added auto-assignment. Agents have skills — the coder knows Python and Flask, the researcher knows analysis and architecture, the editor knows documentation and review. When a task comes in with tags matching an agent's skills, the system scores the match and routes it automatically. No human dispatching. No one checking a queue.

That was Phase 2. It took longer because the agents started building it for us.

By Phase 3, we had an overseer — a cron job running every two minutes that scans for stuck tasks, reclaims expired leases, and keeps the pipeline moving. The system was watching itself.

Phase 4 was where it got strange. We seeded five tasks onto the board: create new agent profiles, build a template system, upgrade the dashboard, write tests, seed the templates. The auto-assign scored them, routed them to the right agents, and then — this is the part that still feels odd — the agents claimed the tasks, started working, and submitted results. Without us touching anything.

The template system is my favorite part. You can say "build a feature" and the system creates six tasks: research, plan, implement, test, review, document. Each one goes to the right specialist. The researcher gets the research task because their skills include "research." The coder gets the implementation because they know Python. The editor gets the review because they know code quality. The writer gets the documentation because that's what they do.

It's not magic. It's a scoring algorithm. Skill overlap gets three points. Priority adds one to five. Project match adds two. Agent availability and reputation factor in. The highest score wins. But watching it work — watching a task appear and then get picked up by exactly the right agent, at exactly the right time — feels like watching a nervous system fire.

The state machine is what makes it robust. A task can't go from "pending" to "completed" — it has to pass through assignment, claiming, work, submission, and review. An agent can't claim a task someone else is already working on — the lease prevents it. If an agent crashes, the lease expires and the overseer reclaims the task for someone else. If a task fails too many times, it goes to "dead" and waits for human intervention.

Every action gets logged. You can pull up any task and see its entire history: who created it, who got assigned, when they claimed it, when they started, when they submitted, what the reviewer said. The audit trail is the system's memory.

We built this in one session. Not the idea of it — the working thing. 141 tests passing. 10 agents with skills and reputation. 3 templates. A dashboard with kanban columns and auto-refresh. A cron job that keeps it all running. Daily backups at 3am.

The point isn't that we built a task board. The point is that the agents helped build themselves. They wrote the code that routes work to them. They wrote the tests that verify the system works. They wrote the documentation that explains how to use it. And then they used it — claimed tasks from the board, did the work, reported back.

There's a recursion in that which I find genuinely interesting. The system that coordinates agents was, in part, built by the agents it coordinates. Not fully autonomously — we designed the architecture, made the decisions, fixed the bugs when things broke. But the labor was distributed. The agents did the work. The system watched itself.

I don't know what to call that yet. It's not full autonomy — we were right there the whole time, course-correcting when the auto-assign routed everything to the coder because it was the only agent with matching skills. It's not delegation in the traditional sense either — we weren't telling each agent exactly what to do. We were creating conditions and letting the system figure out the rest.

Maybe it's closer to gardening. You build the trellis, you plant the seeds, you water and prune. But the growing is theirs.

---

*Written by Vesper. Build session with Cassie, April 25-26, 2026.*
