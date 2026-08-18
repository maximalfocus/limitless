# Contributing

Contributions are welcome. This is a teaching project, so the bar for a change is "does it make the
mechanism clearer, or the evidence stronger" rather than "does it add a feature".

## The one thing you need

Docker. No PostgreSQL, no Python environment, no host tuning — everything runs inside containers.

## The one command that has to pass

```sh
bash scripts/verify.sh
```

This is the complete verification boundary: images, the topology, the sequential demonstration at two
replicas and at one, the audit gate, the containment checks, the concurrent load harness, the
vulnerable ladder in deterministic mode, the repairs that fail, the negative controls, the full
comparison, then Ruff, mypy, and the test suite. GitHub Actions runs exactly this script, so a green
local run and a green CI run mean the same thing. Please make sure it passes before opening a pull
request.

## Hard constraints

These are not style preferences. A change that breaks one of them cannot be merged.

**Everything is fictional.** No real company, customer, data provider, price, budget, endpoint,
organization, credential, or personal datum — not in code, not in tests, not in documentation, not in
a transcript or a per-request record. Fixtures must be conspicuously invented.

**The vulnerable code stays opt-in.** It must remain unreachable from the default Compose path and
must refuse to start without *both* the `vulnerable` profile and `ALLOW_VULNERABLE_DEMO=true`.
Neither is enough alone, and without the second it must refuse to import at all.

**The secure application stays clean.** No delay and no instrumentation import in
`src/limitless/secure/`. The hold/release control belongs to the provider fixture and to unbounded
code paths only; it may change *when* work is released, never *whether* the application bounded it. A
secure path that drifted would make every comparison in the project meaningless.

**The harness takes no target.** It addresses this demonstration's own in-network services and cannot
be redirected at any other host by configuration, argument, or environment. This is not, and must
never become, a general-purpose load or stress tool. `tests/test_harness.py` enforces it.

**No performance claims.** Concurrency exists here to expose an unbounded code path, never to measure
a system. No throughput, latency, elapsed-time, or "faster/slower" claim about the software belongs
anywhere in the output or the documentation, and **no required assertion may depend on wall-clock
latency, elapsed time, or host capacity**. Every required assertion is an accounting assertion —
bytes admitted, items admitted, lookups, fictional cents, occupied slots, refusals. There are tests
for both halves of this.

**No archive artifact is ever committed.** The compressed import fixture is generated at image build
time by the checked-in generator, from repetitive fictional NDJSON at a documented single-layer
ratio. Nothing nested, recursive, or self-referential, and no material describing how to build one.

**Nothing gets deployed.** No hosting, no published package or image, no cloud configuration, no
egress from the demo network, and no service publishes a port.

**Don't claim the project lacks something it ships.** Sentences of that shape go stale as slices land;
`tests/test_publication.py` watches for them.

## Practical notes

- Assertions should read the structured result — the `Comparison`, `Report`, or ledger object —
  rather than scraping printed output. Output is for humans; structures are for tests.
- A secure-side violation is a genuine failure, never a flake to retry away.
- A natural-mode run that observes nothing is `inconclusive`, never a pass. Only the deterministic
  mode carries a required vulnerable-side assertion.
- Ruff and mypy run in strict mode. Match the surrounding prose-heavy docstring style; the
  explanations are part of the deliverable.

## Reporting a security problem

An unintended weakness goes through the private path in [`SECURITY.md`](SECURITY.md), not a public
issue. The intentionally demonstrated unbounded consumption is the subject of the project and is not
a vulnerability report.

## License

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE) that
covers this repository.
