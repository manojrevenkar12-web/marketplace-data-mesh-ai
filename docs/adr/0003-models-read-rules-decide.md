# 0003: Models read; rules and the query engine decide

Status: accepted

## Context

A language model is good at reading logs, descriptions and questions. It is not reliable enough to decide who
sees data, what blocks a merge, or who gets paged at night. It can also be steered by text planted in metadata.

## Decision

* **Ask.** The model drafts SQL from the question and from metadata the person may see. The query engine decides
  whether the SQL runs. Rows never go to the model. Denials are not repaired. Other rejections get one repair
  round.
* **Triage.** Evidence first. Rules decide when they match, and the classifier decides when they do not. The model
  explains, and any citation outside the evidence is removed. If the model disagrees with a rule, the rule wins
  and the disagreement is recorded.
* **Review.** The verdict comes from lint, the diff and lineage. The model writes the comment. A comment that names
  something that does not exist is dropped.
* Every feature is evaluated with a misbehaving model (invalid output, hallucination, obeying injected text,
  flipped answers) and with the model down.

## Consequences

* The worst a model can do is a wrong or missing explanation. That still costs people time, and it is measured:
  with planted instructions, right answers fall from 100% to 65.2% in-sample.
* Features keep working when the model provider is down. Ask then answers nothing, and says so.
