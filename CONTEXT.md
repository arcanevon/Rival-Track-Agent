# Context Glossary

## Terms

### Threat Target

The product, company, or project that the analysis is defending or advising.

A Threat Target is required for every analysis. RivalTrackAgent does not assess threat in the abstract; it assesses competitor pressure relative to this specific target.

The minimum Threat Target profile contains:

- Name
- One-sentence positioning
- Target users
- Core capabilities
- Current competitive concern

### Competitive Threat Level

The assessed pressure that a competitor creates for the Threat Target within a target market or track.

Competitive Threat Level is not the competitor's general market strength, and it is not a threat to the market as a whole. It is always relative to the Threat Target.

Competitive threat is assessed across four dimensions:

- User substitution threat: whether target users may switch from the Threat Target to the competitor.
- Capability catch-up threat: whether the competitor covers or exceeds the Threat Target's core capabilities.
- Distribution threat: whether the competitor can reach the same users more effectively.
- Strategic expansion threat: whether the competitor is likely to enter adjacent territory important to the Threat Target.

An overall threat level may be derived from these dimensions, but it must not replace them.

### Threat Matrix

The Threat Matrix shows competitor pressure relative to the Threat Target.

Rows are competitors. Columns are the four threat dimensions: User substitution threat, Capability catch-up threat, Distribution threat, and Strategic expansion threat. The final column may show the derived overall threat level.

The Threat Matrix must not present overall threat as the only signal.

### Threat Score

Each threat dimension is scored internally on a 0-100 scale.

The user interface displays both a categorical level and the numeric score, such as "High 82" or "Medium 56".

Threat score thresholds are:

- High: 70-100
- Medium: 40-69
- Low: 0-39

The default overall threat score is the equal-weight average of the four dimension scores. Future versions may adjust dimension weights according to the Threat Target's current competitive concern.

### Recommended Response

A Recommended Response is an actionable next step derived from a threat assessment.

Recommended Responses may cover product, growth, and strategy. Each response must resolve to a concrete action rather than a vague recommendation to "monitor" or "pay attention."

Recommended Responses are ordered by priority. Priority is based on threat strength, urgency, and actionability.

### Response Action List

The Response Action List turns Recommended Responses into a prioritized set of concrete actions.

It is a product surface, not just report prose. Each action should be traceable to a threat dimension and supporting evidence.

Response actions are agent-generated suggestions, not approved plans. Actions that imply resource allocation, roadmap commitment, or strategic positioning require human confirmation before execution.

The action list includes:

- Priority
- Response type
- Related threat dimension
- Concrete action
- Evidence basis
- Monitoring signal

Each action must be concrete enough for a product, growth, or strategy owner to open an issue from it. The action list does not need to generate a full PRD.

Each action should expose confidence, evidence strength, and whether human confirmation is required.

### Research Scope

Research Scope is the human-confirmed boundary of one analysis. It contains the
Threat Target, broad track, sub-track, selected competitors, each competitor's
relationship to the target, and a scope version.

The scope is frozen when analysis starts. Newly discovered entities are recorded
as out-of-scope leads and must never silently enter the Threat Matrix.

### Analysis Mode

Analysis Mode is an explicit work-budget policy: fast, standard, or deep. It
controls the competitor cap, search and page-reading budgets, browser fallback,
QA rework rounds, and wall-clock budget. It does not change evidence standards.

### Evidence Grade

Evidence Grade separates discovery from proof:

- Candidate lead: a search result, search snippet, or unread content-page URL.
- Verifiable metadata: public title, author, date, and canonical permalink without body text.
- Citable content evidence: a concrete permalink with readable body text and provenance.

Candidate leads can trigger collection but cannot support a threat score.

### Evidence Portfolio

An Evidence Portfolio is the balanced per-competitor collection contract across
official, benchmark/news, community, and leading-signal sources (O/B/C/L).
Search and page-reading budgets reserve capacity for each source family instead
of consuming the whole budget in query order. Missing source families remain
explicit gaps and are scheduled fairly across competitors during QA rework.

### Report Annotation and Revision

A Report Annotation is a user's comment anchored by report section and quoted
text. It has an intent such as supplement evidence, challenge a conclusion,
rewrite, shorten, or add comparison.

A Report Revision is a proposed section version produced from an annotation. It
keeps the original text, proposed text, evidence identifiers, and QA result.
Only an explicitly accepted revision replaces the current section.

### Evidence Workspace

The Evidence Workspace is a cross-report index of stable evidence identifiers,
source snapshots, source status, and reports that cite them. It supports reuse
without treating historical conclusions as current evidence.

### Human Correction Metric

Human Correction Metrics measure accepted and rejected AI revisions, manually
edited conclusions, rejected evidence types, and confidence changes after
supplemental collection. They are feedback signals, not automatic model scores.
