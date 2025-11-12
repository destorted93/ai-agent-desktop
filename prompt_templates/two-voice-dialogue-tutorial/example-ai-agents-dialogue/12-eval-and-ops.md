F: Keeping it working—what do we watch and how?
M: Two lanes. Offline—replay past tasks and fuzz tricky cases. Online—real tasks with guardrails and gradual rollout.
F: Dials, please.
M: Four: success rate, time‑to‑done, human assists, and spend. Set SLOs like “80% finish under two minutes and two dollars.”
F: And traces?
M: Name every step; store inputs and outputs—scrubbed—plus latency, cost, and decisions. Enough to replay without guessing.
F: When it breaks?
M: Retries with backoff, circuit breakers for flaky tools, fallbacks to simpler behavior, and a kill switch. Incident playbooks so everyone knows who does what.
F: Boring runs are the dream.
M: Exactly. Last bit—clear up the common myths so teams don’t trip on folklore.