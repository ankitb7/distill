# Finding actionable, cutting-edge engineering articles

Distill's target is not “interesting AI news.” It is a short Reading slate that changes an
engineering decision, experiment, workflow, or implementation for this Reader profile.

## What “worth reading” means

An Article earns attention through six independent questions:

1. **Personal relevance** — does it advance a current outcome such as Easel's internal feature
   factory, production coding-agent infrastructure, Frontend DevEx, AI enablement, large-scale
   migrations, or coordinated agent swarms?
2. **Technical depth** — does it expose mechanisms, architecture, constraints, trade-offs, and
   failure modes rather than naming a trend?
3. **Novel insight** — is there a transferable finding that is new relative to the archive? A new
   launch or event is not automatically a new insight.
4. **Actionability** — can the reader run an experiment, make a decision, change a pipeline, reuse an
   artifact, or adopt a technique within weeks?
5. **Evidence quality** — is the claim supported by code, measurements, evaluations, incident data,
   or a first-hand production case study?
6. **Noise risk** — is it repackaged consensus, hype, vague futurism, unevidenced prediction,
   second-hand news, or commentary without a mechanism or action?

The dimensions must remain separate. “Cutting edge,” “popular,” “long,” and “from a trusted author”
are discovery signals; none proves substance.

## Research translated into mechanism

### Use an explicit multidimensional rubric

LLM judges can prefer superficial qualities such as fluency or verbosity, and their scores can move
under irrelevant perturbations. A detailed rubric helps, but it still needs calibration against
human judgment. Distill therefore asks for independent dimensions, clamps every value, versions the
rubric and Reader profile, and records failures instead of treating them as successful scores.

- [Mitigating the Bias of Large Language Model Evaluation](https://aclanthology.org/2024.ccl-1.101/)
- [Judging the Judges: position bias in LLM-as-a-Judge](https://aclanthology.org/2025.ijcnlp-long.18/)
- [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

### Treat novelty as marginal information, not recency

Novelty is relative to what has already been read. Temporal novelty detection discounts repeated
language over time, while Maximal Marginal Relevance selects the next relevant document partly by
how little it duplicates the selected set. Distill initially applies an inexpensive lexical MMR
reranker and domain/source caps. A later embedding adapter can improve semantic comparison without
changing the Reading-slate interface.

- [Goldstein and Carbonell: MMR diversity reranking](https://aclanthology.org/X98-1025/)
- [Using temporal IDF for novelty detection in text streams](https://arxiv.org/abs/1401.1456)

### Keep retrieval, assessment, and slate selection distinct

Candidate generation needs high recall and freshness; assessment estimates value; slate selection
controls redundancy. Large recommendation systems similarly separate candidate generation from
ranking and explicitly account for freshness. Distill's Collect keywords should therefore discover
broadly, while the Reader profile performs semantic assessment rather than literal filtering.

- [Deep Neural Networks for YouTube Recommendations](https://research.google.com/pubs/archive/45530.pdf)

### Explanations must expose a usable reason

An explanation helps both trust and debugging only when it is grounded in item features. Distill
stores a recommended action and evidence-based reasoning, rather than a bare composite number.

- [Explainable Recommendation: A Survey and New Perspectives](https://arxiv.org/abs/1804.11192)
- [Knowledge-grounded Natural Language Recommendation Explanation](https://arxiv.org/abs/2308.15813)

## Calibration plan

Start with 20–40 real Articles and label each `must-read`, `useful`, `skip`, or `noise`, plus the
action it enabled. Include hard contrasts: a polished prediction versus a measured migration case
study, a product launch versus its architecture post, and a generic “agents are the future” essay
versus a failure analysis with code.

Track:

- precision at 10 for `must-read` + `useful`;
- noise rate in the first 10;
- must-read recall across the candidate pool;
- agreement between human labels and each assessment dimension;
- source/domain/topic concentration in the Reading slate;
- score changes whenever the rubric, model, or Reader profile changes.

Anthropic reports that useful evaluation loops can begin with roughly 20 representative cases and
should combine automated checks with periodic human review. The labels—not engagement—become the
ground truth for tuning weights and thresholds.

- [Anthropic: How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Anthropic Bloom: judge trust and calibration](https://alignment.anthropic.com/2025/bloom-auto-evals/)

## Follow-up depth

The current implementation deliberately keeps a small interface: assess pending Articles, then
select a Reading slate. Next improvements can sit behind that seam:

- semantic novelty against the previously read archive;
- immutable assessment attempts with content fingerprints;
- `read`, `skip`, and `actioned` feedback capture;
- pairwise calibration for close calls near the slate cutoff;
- learned weights once enough personal labels exist.
