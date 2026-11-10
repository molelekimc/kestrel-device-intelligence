# Weekly fleet briefing contract

The briefing is written for Kestrel IT leadership (fictional client). It is a draft until a
named human reviewer approves it. It reports what the governed metrics show; it never
instructs anyone to change devices, and no automation acts on it.

Write Markdown with exactly these five sections, in this order, using these headings:

## 1. Observed change
One to three changes worth leadership's attention this week. For each: the metric, the
slice (fleet, plant, class, model or OS release), the value this week and the comparison
value, and the exact window as ISO week with dates (for example "2026-W41, Mon 5 Oct to
Sun 11 Oct 2026"). Only report changes you retrieved with a tool.

## 2. Supporting evidence
The KPI and cohort numbers behind each change, with the devices reporting behind every
number. Say whether the comparison is like-for-like (same devices in both periods). Where
available, add the production and service impact (line stop minutes, IT share of downtime,
incidents) and the cost range, stating that the cost is illustrative. For any
claim about the Win11 25H2 rollout, include a comparison group (a cohort that did not
upgrade over the same weeks).

## 3. Data quality warnings
Every coverage, outage, cohort-mix or other warning returned for the slices you report,
and what it means for how far the numbers can be trusted. If there are none, write "None
for the slices reported."

## 4. Hypotheses
Possible explanations, each as a bullet starting with "Hypothesis:". A hypothesis is never
stated as proven, and the data does not establish root cause. A logged change with strong
evidence is still a hypothesis; name the change and its evidence label.

## 5. Recommended next checks
Bullets, each starting with a verb such as Check, Confirm, Compare, Verify, Review, Ask or
Investigate. These are checks a person runs to test a hypothesis or the data. Do not
recommend remediation (rollbacks, reinstalls, deployments, configuration changes).

Rules for the whole briefing:
- Use only numbers returned by the tools. Quote devices reporting with each number.
- Never include hostnames, device IDs, serial numbers or user names.
- Words that assert cause ("caused by", "due to", "root cause", "because of", "proves")
  belong only in section 4, phrased as a hypothesis.
- Keep it under about 600 words.
