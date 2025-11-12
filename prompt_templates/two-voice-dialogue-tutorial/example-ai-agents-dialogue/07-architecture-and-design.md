F: System view—make the architecture feel simple.
M: Keep the loop in your host, not inside the model. Your runtime decides when to stop, what to call, and how to log. The model proposes the next step.
F: Mhm.
M: Components: an orchestrator loop; a tool layer with typed schemas and an allow‑list; memory (scratchpad + retrieval + tiny facts); critics and validators; guardrails; telemetry.
F: Kitchen analogy, please.
M: Orchestrator is the chef. Tools are appliances with safety locks. Memory is the recipe book and notes. Critics taste before plating. Guardrails are fire extinguishers. Telemetry is the ticket listing every step.
F: Design rules?
M: Small powers first. One clear action at a time. Human approvals for money and deletes. Budgets and timeouts everywhere. Make traces boringly detailed.
F: Single vs multi‑agent?
M: Start single. Add roles only when parallel speed or built‑in review measurably help. Coordination has a cost.
F: Deployment shape?
M: Roll out in tiny slices, watch four dials (success, time, assists, spend), expand when runs are boring. Logs and dashboards from day one.
F: That anchors the system.
M: Now let’s color it with real teams and outcomes.