# 0006: Evaluation from a simulated world, and blind held-out sets

Status: accepted

## Context

Claims like "the triage routes failures correctly" need ground truth. Real incident data with known causes is
rarely available, and it is never available to someone outside the company.

## Decision

* `mesh.sim` simulates the marketplace for four months and injects incidents of known kinds. It records the truth
  in `data/truth`. History incidents become on-call tickets with 8% mislabels, to train the classifier.
* Only `mesh.sim` and `mesh.eval` may read the truth. `tests/test_isolation.py` checks that.
* The release gate (`config/release.yaml`) fails on any miss.
* Blind held-out sets are written from a guide by someone who has not seen the code. They are run once, and the
  first run is pinned by sha256 and never rewritten. Studying a set's failures turns it into a regression set.

## Consequences

* The in-sample numbers are high, and the blind numbers are lower. Both are published (`heldout/README.md`).
* The simulator's realism limits what the evaluation can show. A real model on real logs remains to be measured.
