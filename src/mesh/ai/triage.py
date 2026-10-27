"""Why did my pipeline fail? Evidence first, then rules, a classifier, and a model to explain it.

On-call for a data mesh spends most of its time finding out *whose* problem a failure is. A consumer's job
fails because an upstream team renamed a column, a source system shipped prices as text, or the platform
lost a grant, and the alert goes to whoever owns the failed job. The triage:

1. collects evidence across systems: the run's error and log lines, the upstream runs for the same day
   (walking to the first failure), contract and transform versions published shortly before the run (as of
   the run's time, not later), and the quality results;
2. applies rules that are exact about the cases they know (a column the transform reads was renamed in an
   input's contract two days ago);
3. scores the error text with a classifier trained on resolved on-call tickets, for wording the rules do
   not know;
4. asks a model to choose among the candidates, cite the evidence and write the explanation and next steps.
   Citations are checked; a model that disagrees with a confident rule does not change the category.

The result names the cause, the evidence, and the team that has to act, which is the point.
"""

from __future__ import annotations

import pickle
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from mesh import config
from mesh.contracts import Registry
from mesh.history import Versions
from mesh.llm.base import Block, Message, ModelRequest, ModelResponse
from mesh.llm.prompts import load
from mesh.llm.router import Budget, ModelRouter, Unavailable
from mesh.llm.schemas import RECORD_DIAGNOSIS, DiagnosisDraft
from mesh.state import Run, State

COLUMN_MISSING = re.compile(
    r'Referenced column "?(\w+)"? not found|column "?(\w+)"? (?:does not exist|not found)|does not have a column named "?(\w+)"?'
    r"|Column '(\w+)' cannot be resolved|Values list .*? does not have a column named \"?(\w+)", re.I)


@dataclass(frozen=True)
class Evidence:
    id: str
    source: Literal["run", "log", "upstream", "contract", "transform", "quality"]
    text: str


@dataclass
class Candidate:
    category: str
    source: Literal["rule", "classifier"]
    confidence: float
    why: str
    evidence: list[str]
    acts_on: str = ""  # product whose owner must act (for upstream and source cases)


@dataclass
class Diagnosis:
    run_id: str
    product: str
    dt: str
    status: str
    category: str
    route_to: str
    naive_route: str  # who a plain "job failed" alert would page: the failed product's owner
    decided_by: str  # rule | classifier | model | rule (model disagreed) | uncertain
    confidence: float
    root_run: str
    summary: str
    next_steps: list[str]
    evidence: list[Evidence]
    cited: list[str]
    candidates: list[Candidate]
    sources_consulted: list[str]
    runbook: str = ""
    model: str | None = None
    model_problems: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TriageClassifier:
    """TF-IDF over the error and log text, logistic regression, trained on resolved on-call tickets."""

    def __init__(self, vectorizer: Any = None, model: Any = None):
        self.vec = vectorizer
        self.model = model

    @staticmethod
    def text(run: Run, log: list[str]) -> str:
        lines = [x.split("] ", 1)[-1] for x in log if " ERROR " in x or " WARN " in x]
        return " ".join([run.step, run.error_type or "", run.error or "", *lines])

    @classmethod
    def train(cls, texts: list[str], labels: list[str]) -> TriageClassifier:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression

        vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True, token_pattern=r"[A-Za-z_][A-Za-z_]+")
        X = vec.fit_transform(texts)
        model = LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced").fit(X, labels)
        return cls(vec, model)

    def predict(self, text: str) -> list[tuple[str, float]]:
        if self.model is None:
            return []
        p = self.model.predict_proba(self.vec.transform([text]))[0]
        return sorted(zip(self.model.classes_, (float(x) for x in p), strict=True), key=lambda t: -t[1])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps({"vec": self.vec, "model": self.model}))

    @classmethod
    def load(cls, path: Path) -> TriageClassifier:
        if not path.exists():
            return cls()
        d = pickle.loads(path.read_bytes())  # noqa: S301 - our own artifact, written by `mesh train`
        return cls(d["vec"], d["model"])


def train_from_tickets(state: State, tickets_path: Path) -> tuple[TriageClassifier, int]:
    import json

    texts, labels = [], []
    for line in tickets_path.read_text().splitlines() if tickets_path.exists() else []:
        t = json.loads(line)
        run = state.run(t["run_id"])
        if run is None or run.status == "upstream_failed":
            continue  # a blocked run's text says nothing about the cause; the triage walks upstream instead
        texts.append(TriageClassifier.text(run, state.log(run.id)))
        labels.append(t["root_cause"])
    if len(set(labels)) < 2:
        return TriageClassifier(), len(labels)
    return TriageClassifier.train(texts, labels), len(labels)


def _parse(resp: ModelResponse) -> DiagnosisDraft:
    return DiagnosisDraft.model_validate(resp.tool_input("record_diagnosis"))


class Triage:
    def __init__(self, state: State, versions: Versions, router: ModelRouter, classifier: TriageClassifier | None = None):
        self.state = state
        self.versions = versions
        self.router = router
        self.classifier = classifier or TriageClassifier()
        self.cfg = config.load("triage")

    # ---------------------------------------------------------------- public

    async def diagnose(self, run_id: str, use_model: bool = True) -> Diagnosis:
        run = self.state.run(run_id)
        if run is None:
            raise KeyError(run_id)
        registry = self.versions.registry_at(run.dt)
        evidence: list[Evidence] = []
        consulted: list[str] = ["run record", "run log"]
        root = self._root(run, evidence, consulted)
        cands = self._rules(root, registry, evidence, consulted)
        cls_cands = self._classify(root, evidence)
        all_cands = sorted(cands, key=lambda c: -c.confidence) + cls_cands
        cats = list(self.cfg["categories"])
        diag = Diagnosis(
            run_id=run.id, product=run.product, dt=run.dt, status=run.status, category="unknown", route_to="", naive_route=registry[run.product].owner,
            decided_by="uncertain", confidence=0.0, root_run=root.id, summary="", next_steps=[], evidence=evidence, cited=[],
            candidates=all_cands, sources_consulted=sorted(set(consulted)),
        )
        best_rule = max(cands, key=lambda c: c.confidence) if cands else None
        best_cls = cls_cands[0] if cls_cands else None
        if best_rule:
            self._decide(diag, best_rule, "rule", registry, root)
        elif best_cls and best_cls.confidence >= float(self.cfg["classifier_min_probability"]):
            self._decide(diag, best_cls, "classifier", registry, root)
        if use_model:
            await self._explain(diag, run, cats, all_cands, registry, root)
        if not diag.summary:
            c = best_rule or best_cls
            diag.summary = (f"{run.id}: {c.why}" if c else f"{run.id} failed at {run.step}: {run.error_type}. No rule or ticket pattern matches; "
                                                                f"read the log and add a rule once the cause is known.")
        if diag.category in self.cfg["categories"]:
            step = self.cfg["categories"][diag.category]["step"]
            if step not in diag.next_steps:
                diag.next_steps = [step, *diag.next_steps][:5]
            diag.runbook = self.cfg["categories"][diag.category]["runbook"]
        if root.id != run.id:
            diag.next_steps.append(f"This run was blocked by {root.id}; it re-runs automatically when that partition is published.")
        self.state.add_usage({"feature": "triage", "run": run.id, "category": diag.category, "decided_by": diag.decided_by,
                              "route_to": diag.route_to, "cost_usd": diag.cost_usd})
        return diag

    # ---------------------------------------------------------------- evidence

    def _add(self, evidence: list[Evidence], source: Any, text: str) -> str:
        eid = f"E{len(evidence) + 1}"
        evidence.append(Evidence(eid, source, text[:400]))
        return eid

    def _root(self, run: Run, evidence: list[Evidence], consulted: list[str]) -> Run:
        """Follow blocked runs upstream to the first run that failed for its own reasons."""
        current = run
        seen = set()
        while current.status == "upstream_failed" and current.blocked_by and current.id not in seen:
            seen.add(current.id)
            self._add(evidence, "run", f"{current.id} did not run: {current.error}")
            consulted.append("upstream runs")
            nxt = None
            for upstream in current.blocked_by:
                before = [r for r in self.state.runs(upstream, current.dt) if r.ended <= current.started or r.started <= current.started]
                if before and not before[-1].ok:
                    nxt = before[-1]
                    self._add(evidence, "upstream", f"{nxt.id} {nxt.status} at {nxt.step}: {nxt.error_type}: {(nxt.error or '')[:200]}")
                    break
            if nxt is None:
                break
            current = nxt
        if current.status != "upstream_failed":
            self._add(evidence, "run", f"{current.id} {current.status} at step {current.step}: {current.error_type}: {(current.error or '')[:240]}")
            for line in self._log_lines(current):
                self._add(evidence, "log", line)
        return current

    def _log_lines(self, run: Run) -> list[str]:
        lines = [x for x in self.state.log(run.id) if " ERROR " in x or " WARN " in x]
        out: list[str] = []
        for x in lines:
            if x not in out:
                out.append(x)
        return out[:8]

    # ---------------------------------------------------------------- rules

    def _rules(self, run: Run, registry: Registry, evidence: list[Evidence], consulted: list[str]) -> list[Candidate]:
        et, err = run.error_type or "", run.error or ""
        run_ev = [e.id for e in evidence if e.source in ("run", "upstream") and e.text.startswith(run.id)] or [evidence[-1].id]
        log_ev = [e.id for e in evidence if e.source == "log"]
        cands: list[Candidate] = []
        text = f"{et} {err}"
        lookback = int(self.cfg["lookback_days"])
        since = (date.fromisoformat(run.dt) - timedelta(lookback)).isoformat()

        if et == "LandingFileNotFound":
            cands.append(Candidate("late_drop", "rule", 0.95, f"the source system's drop for {run.dt} was not in the landing zone", run_ev, run.product))
        if et == "ContractViolation" and run.step == "ingest":
            cands.append(Candidate("schema_drift", "rule", 0.9, "the source drop lacks a contract column and carries a field the contract does not "
                                                              "declare: the source system renamed it", run_ev + log_ev[:2], run.product))
        if et in ("TypeCastError", "MalformedInput") and run.step == "ingest":
            cands.append(Candidate("type_drift", "rule", 0.9, "the source sent values that cannot be read as the contract's type", run_ev, run.product))
        if et == "QualityCheckFailed":
            consulted.append("quality results")
            failed = [q for q in self.state.quality(run.product, run.dt) if not q.passed and q.severity == "error"]
            qev = [self._add(evidence, "quality", f"{q.check} failed: {q.detail}") for q in failed[-3:]]
            checks = " ".join(q.check for q in failed) or err
            if "unique" in checks:
                cands.append(Candidate("duplicates", "rule", 0.9, "the partition holds duplicate keys", qev or run_ev, run.product))
            if "not_null" in checks or "null_share" in checks:
                cands.append(Candidate("null_spike", "rule", 0.88, "a key column is suddenly empty on many rows", qev or run_ev, run.product))
            if "volume" in checks:
                ratios = [q.observed for q in failed if q.check.startswith("volume") and isinstance(q.observed, int | float)]
                if not ratios or min(ratios) < 1:
                    cands.append(Candidate("volume_drop", "rule", 0.85, "far fewer rows than the recent median", qev or run_ev, run.product))
        if run.step == "transform" and et not in ("AccessDeniedException", "ResourceExhausted", "ServiceError"):
            consulted.append("contract history")
            m = COLUMN_MISSING.search(err)
            col = next((g for g in m.groups() if g), None) if m else None
            c = registry[run.product]
            for i in c.inputs:
                for v, changes in self.versions.changes(i, since, run.dt, published_before=run.started):
                    for ch in changes:
                        relevant = ch.kind in ("column_removed", "column_renamed", "type_changed") and (col is None or ch.column == col)
                        if relevant:
                            eid = self._add(evidence, "contract", f"{i} v{v.version} effective {v.effective} by {v.author} "
                                                                  f"('{v.note}'): {ch.detail}")
                            conf = 0.93 if col else 0.7
                            cands.append(Candidate("upstream_contract_change", "rule", conf,
                                                   f"{i} changed its contract on {v.effective} ({ch.detail}) and {run.product} still reads the old column",
                                                   [*run_ev, eid], i))
            for v, changes in self.versions.changes(run.product, since, run.dt, published_before=run.started):
                if any(ch.kind == "transform_changed" for ch in changes):
                    consulted.append("transform history")
                    prev = [h for h in self.versions.history(run.product, run.started) if h.effective < v.effective or h.published < v.published]
                    diff_text = _sql_diff(prev[-1].sql if prev else "", v.sql or "")
                    eid = self._add(evidence, "transform", f"{run.product} transform changed, effective {v.effective} by {v.author} ('{v.note}'): {diff_text}")
                    in_new = bool(col and v.sql and re.search(rf"\b{re.escape(col)}\b", v.sql))
                    conf = 0.9 if (in_new or not col) else 0.6
                    if not any(cd.category == "upstream_contract_change" and cd.confidence > conf for cd in cands):
                        cands.append(Candidate("code_change", "rule", conf, f"{run.product}'s own transform changed just before the failure "
                                                                          f"({diff_text})", [*run_ev, eid], run.product))
        if "AccessDenied" in text or "not authorized" in text or "Insufficient Lake Formation" in text:
            cands.append(Candidate("access_denied", "rule", 0.95, "the pipeline role lacks a Lake Formation or S3 permission", run_ev, run.product))
        if re.search(r"out of memory|exhausted resources|oom", text, re.I):
            cands.append(Candidate("resource_exhausted", "rule", 0.93, "the worker ran out of memory", run_ev, run.product))
        if re.search(r"SlowDown|ServiceUnavailable|temporarily unavailable|read timeout|Could not connect|throttl", text, re.I):
            cands.append(Candidate("transient_infra", "rule", 0.85, "AWS throttled or failed after the retries", run_ev, run.product))
        return cands

    def _classify(self, run: Run, evidence: list[Evidence]) -> list[Candidate]:
        preds = self.classifier.predict(TriageClassifier.text(run, self.state.log(run.id)))
        ev = [e.id for e in evidence if e.source in ("run", "log")][:3]
        return [Candidate(c, "classifier", round(p, 3), f"resolved tickets with similar errors were {c}", ev, run.product)
                for c, p in preds[:2] if p >= 0.15]

    def _decide(self, diag: Diagnosis, c: Candidate, how: str, registry: Registry, root: Run) -> None:
        diag.category, diag.confidence, diag.decided_by = c.category, c.confidence, how
        diag.cited = list(c.evidence)
        diag.route_to = self._route(c, registry, root)

    def _route(self, c: Candidate, registry: Registry, root: Run) -> str:
        acts = self.cfg["categories"].get(c.category, {}).get("acts", "product_owner")
        if acts == "platform":
            return str(self.cfg["platform_team"])
        if acts == "upstream_owner" and c.acts_on in registry:
            return registry[c.acts_on].owner
        product = c.acts_on if c.acts_on in registry else root.product
        return registry[product].owner

    # ---------------------------------------------------------------- model

    async def _explain(self, diag: Diagnosis, run: Run, cats: list[str], cands: list[Candidate], registry: Registry, root: Run) -> None:
        prompt = load("triage")
        cand_text = "\n".join(f"- {c.category} ({c.source}, {c.confidence:.2f}): {c.why} [evidence: {', '.join(c.evidence)}]" for c in cands) or "(none)"
        ev_text = "\n".join(f"[{e.id}] ({e.source}) {e.text}" for e in diag.evidence)
        user = prompt.render_user(run=f"{run.id} ({run.status})", categories=", ".join(cats), candidates=cand_text, evidence=ev_text)
        req = ModelRequest(task="triage", system=prompt.system, messages=[Message("user", [Block.t(user)])], tools=[RECORD_DIAGNOSIS],
                           tool_choice="record_diagnosis", max_tokens=900, prompt_id=prompt.id, prompt_version=prompt.version, data_scope="internal")
        budget = Budget(limit_usd=float(self.router.routing.get("max_cost_usd_per_request", 0.2)))
        try:
            routed = await self.router.call(req, _parse, budget)
        except Unavailable as e:
            diag.model_problems.append(f"model unavailable: {e}")
            return
        d = routed.value
        diag.model, diag.cost_usd = routed.model.key, round(budget.spent_usd, 6)
        known = {e.id for e in diag.evidence}
        bad = [x for x in d.evidence if x not in known]
        if bad:
            diag.model_problems.append(f"cited evidence that does not exist: {', '.join(bad)}")
        if d.category not in cats:
            diag.model_problems.append(f"category '{d.category}' is not allowed")
        if diag.model_problems:
            return  # an explanation with invented citations is not shown; the rule's or classifier's stands
        if diag.decided_by == "rule":
            if d.category != diag.category:
                diag.model_problems.append(f"model chose {d.category}; the rule's {diag.category} stands")
                diag.decided_by = "rule (model disagreed)"
                return
        elif diag.decided_by == "classifier":
            if d.category != diag.category:
                match = next((c for c in cands if c.category == d.category), None)
                if match:
                    self._decide(diag, match, "model", registry, root)
        else:
            match = next((c for c in cands if c.category == d.category), None)
            if match is None or not d.evidence:
                diag.model_problems.append("model's category has no candidate or no evidence")
                return
            self._decide(diag, match, "model", registry, root)
            diag.confidence = min(match.confidence, 0.6)
        diag.summary = d.summary
        diag.cited = sorted(set(diag.cited) | set(d.evidence), key=lambda x: int(x[1:]))
        diag.next_steps = d.next_steps


def _sql_diff(old: str, new: str) -> str:
    a, b = old.split(), new.split()
    removed = [w for w in a if w not in b]
    added = [w for w in b if w not in a]
    return f"removed {' '.join(removed[:6]) or '-'}; added {' '.join(added[:6]) or '-'}"
