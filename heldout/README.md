# Held-out sets

The numbers in `reports/eval/` come from cases written alongside the code: the 28 questions in
`src/mesh/eval/questions.py`, the generated contract changes, the incidents of the simulated world. They are
in-sample. Code tuned against them can reach 100% without being good at anything else.

So there are blind sets too. Each one was written from a guide alone (`WRITING_QUESTIONS.md`,
`WRITING_INCIDENTS.md`, `WRITING_CHANGES.md`) by someone who had not seen the code. Each was run once with
`mesh heldout run <set>`. That first run is pinned in `reports/heldout/<set>/first_run.json`, together with the
sha256 of the set's files.

| Set | What it tests | First run (2026-10-27, simulated model) |
| --- | --- | --- |
| `questions-1` | 30 questions in English and German, including refusals and row-filtered users | Answered right 47.8%; wrong answers shown 5; not answered 3; refused correctly 57.1% (3 answered when they should have been refused); **data shown that the person may not see: 0** |
| `changes-1` | 24 contract and transform changes, 8 of them safe | Verdict as expected 100%; harmful blocked 100%; safe changes blocked by mistake 0 of 8 |
| `incidents-1` | 24 failures written as run logs and history | Right cause 58.3%; right team 79.2% (a plain alert to the failed job's owner: 58.3%) |

## How to read these numbers

The access controls held on cases nobody tuned for: no blind question showed data the person may not see.
The change review kept its in-sample result.

The assistant and the triage did not. The in-sample results are 100% for questions and 100% for triage. On the
blind sets they are 47.8% and 58.3%. The likely reasons are below. They are not verified: checking them would
mean studying the failures, and that would end the sets' blind status.

* The simulated model's SQL drafts come from a rule-based parser (`mesh.llm.sim_sql`). It was written for the
  in-sample phrasings. A real model is expected to do better on new phrasings; that has not been measured here.
* The triage rules recognise the log shapes of the simulator. Hand-written logs that say the same thing in other
  words fall through to the ticket classifier or to the model.

Also recorded as observed, not fixed: 4 gold queries in `questions-1` scan more partitions than the
partition-filter rule allows. So the evaluator could not compute their answers either. They count against the
assistant in the first run, as recorded.

## Rules

* `reports/heldout/<set>/first_run.json` is never rewritten.
* The release gate fails if a set's files no longer match the sha256 recorded in its first run
  (`held-out sets: files unchanged since their first run`).
* `tests/test_heldout_and_gate.py` checks the same thing.
* Later runs write `latest.json`, which is not committed.
* Never change the code to pass a set. If you study a set's failures, write `regression` to its `STATUS` file.
  From then on it is a regression set. Then have someone who has not seen the code write a new blind set
  (`questions-2`, …) from the guide only.
* `STATUS` is not part of the pin, so marking a set as a regression set does not break the gate.

None of the three sets has been studied yet. All three are still `blind`.
