# limitless

**Local educational material.** A small, container-only demonstration of **unrestricted resource
consumption** — what happens when nothing on the request path bounds *how much work a caller may
name*, and what it takes to bound every dimension of it.

Everything here is fictional: the company, its customers, the data provider, the prices, the
budgets, and the credentials. The demonstration runs on a hermetic container network with **no
egress**, publishes no ports, contacts no real service, and directs traffic at nothing but the
services it starts itself.

> This project is a work in progress. It is not deployed, not hosted, and not intended to be.

## The idea in one line

A request names work, and the request's own size tells you nothing about how much.

Sixty bytes of query string can name a million rows. Thirty-six kilobytes of gzip can name ten
megabytes of records. One ordinary-looking `POST` can name fifty thousand metered lookups at a
provider that bills for every one of them. The gap between what a request *costs the caller* and
what it *costs you* is the vulnerability, and the ratio between those two numbers is the number that
matters.

## What is here now

The **secure** side of the demonstration: a fictional business-to-business enrichment API
(*Halyard Insights*) that bounds all five dimensions a caller can otherwise drive without limit.

| Dimension | Bound |
|---|---|
| how big | request body size, enforced **while reading the stream**, before anything is buffered |
| how many | batch item count, and any caller-supplied page size |
| how much it becomes | decompressed-byte ceiling **and** expansion ratio, enforced **during** decompression |
| how often | a per-tenant allowance charged in **provider lookups**, reserved by one atomic conditional write |
| how many at once | an explicit in-flight cap with excess **shed** rather than queued, plus deadlines that **cancel** |

Alongside it runs *Coastwise Registry*, a fictional metered provider operated as its own in-network
service. It keeps its own ledger and reports it through its own endpoint, so the cost of a piece of
work is read off **the provider's bill** rather than asserted by the application under study.

The application runs as **two replicas over one shared PostgreSQL instance**. That is part of the
mechanism, not a deployment choice: an allowance held inside one process silently becomes two
allowances the moment a second process serves the same endpoint.

## The harness

A containerized **concurrent load and amplification harness** drives the demonstration. Every request
in a round is released on a single barrier, so the application really does see them arrive together,
and every round mixes three things at once: ordinary legitimate work, reads of an endpoint that needs
no provider and no budget, and probes naming more than a bound allows.

Each run ends in an accounting whose central number is the **amplification ratio** — fictional cents
admitted per byte of input — beside the spend cap and its remaining balance, peak occupancy against
capacity, cheap-endpoint availability, refusals by kind, and an explicit **bounded / unbounded**
verdict. The money in it is read from the provider's own ledger, and the transcript is written to
`artifacts/` as the run artifact behind every claim.

Against the secure application the harness asserts, in **every** round and at one replica and at two:
every tenant charged against its own allowance only, the global cap never breached, no bystander
affected by another tenant's spending, in-flight work never over its cap, the cheap endpoint
answering every request, and **zero** bound violations. Those exact assertions are also what prove
the harness genuinely generates load — a harness that generated none would satisfy them without
trying.

## The unbounded ladder

An **opt-in, intentionally vulnerable** variant of the same API demonstrates the four shapes the
bounds exist to prevent. It is not started by the default path, and starting it takes **two**
deliberate actions — the `vulnerable` Compose profile *and* `ALLOW_VULNERABLE_DEMO=true`. Neither is
enough alone, and without the second the application refuses to import at all.

| Shape | What it does |
|---|---|
| the client names the work | one request naming 50 000 records bills **0.80x** the whole monthly cap; 18 bytes of query string serialize 50 400 records |
| repetition against an un-partitioned budget | one tenant drains the shared pool to zero, and the tenants that spent nothing are refused |
| expansion, checked wrongly | 169 KB of gzip admits work worth **11.5x** the entire monthly cap, recorded durably as it is admitted |
| unbounded in-flight work, no deadline | held calls occupy every connection and an endpoint that needs no provider stops being served |

The **deterministic** reproduction mode makes the last of those arithmetic rather than a race with
the clock: the provider fixture is held, exactly as many calls as the replica has connections are put
in flight, and only once the fixture's own count confirms it is the cheap endpoint asked whether it
can still be served. That instrumentation lives in the provider fixture and in unbounded code paths
only — never in a secure one — and it changes only *when* work is released, never whether the
application bounded it.

The compressed import fixture is **generated at image build time** by a checked-in generator, from
repetitive fictional NDJSON at a documented single-layer ratio of about 294:1. No archive is ever
committed to this repository.

## The repairs that fail

Four repairs a competent engineer reaches for. Each is genuinely implemented, each is genuinely
honoured, and each fails anyway — which is why they are worth building.

| Repair | Honoured | Fails because |
|---|---|---|
| a request-count rate limit | 60/min, **0 violations** against the caller | it counts requests; the resource is lookups. 62 440 lookups drained the whole cap from inside the limit |
| an in-process allowance | exactly **40 000** at one replica | it is one allowance *per process*: **80 000** at two replicas, with nothing else changed |
| a caller-keyed allowance | exactly **40 000** on a steady key | the key is a bucket the caller can mint a fresh one of, by changing a header |
| a deadline that returns | the caller is answered at 1s | it bounds the *response*. The upstream call kept running, kept its slot, and billed anyway |

...plus the size check that is present, honoured, and useless: it refused 281 KB and waved through
169 KB worth **11.5x** the entire cap, because it measures the compressed number *after* the body is
already in memory.

## What this flaw is not

Three negative controls mark the boundary, so nobody leaves with the wrong repair in mind.

- **Every request is authenticated and authorized.** No object-level, function-level or
  property-level authorization control would refuse a single one of them.
- **One of each request is perfectly correct.** A functional suite written against them is entirely
  green. The defect lives only in the aggregate, which is what functional suites do not assert on —
  and that is the reason it ships.
- **More capacity is not a fix.** Doubling the budget doubled the time-to-drain from 32 requests to
  63 and changed the amplification ratio by **0.0015**. Capacity buys a constant factor; the fix has
  to change the structure.

## Running it

Requires **Docker** and nothing else — no PostgreSQL, no Python environment, no host tuning.

```sh
bash scripts/demo.sh      # the sequential demonstration
bash scripts/verify.sh    # the complete boundary: demo, harness, ladder, containment, tests
```

The vulnerable variant, if you want to drive it by hand:

```sh
ALLOW_VULNERABLE_DEMO=true docker compose --profile vulnerable up --detach --wait vuln-a vuln-b
docker compose run --rm harness python -m limitless.harness --variant vulnerable
docker compose run --rm harness python -m limitless.harness --variant half-fixes
docker compose run --rm harness python -m limitless.harness --variant controls
```

Every scenario in one table, which is the fastest way to see the point:

```sh
docker compose run --rm compare              # the comparison
docker compose run --rm compare --verbose    # ...and the per-request records behind it
```

```
secure scenarios     : 2, every one bounded, worst amplification 0.0199 cents per input byte
unbounded scenarios  : 12, worst amplification 17.0672 cents per input byte
the ratio between them: 856x
```

[`WALKTHROUGH.md`](WALKTHROUGH.md) explains all of it: the five dimensions, amplification as a ratio,
the taxonomy and its caveats, the four shapes with the repair that fails against each, the three
negative controls, and all five secure controls with their trade-offs.

A documented run parameter selects how many replicas are addressed:

```sh
LIMITLESS_REPLICAS=1 bash scripts/demo.sh
```

## Safety

- The network is `internal: true`. There is **no egress**, and no service publishes a port.
- The demonstration's targets are its own in-network services and **cannot be redirected** at any
  other host by configuration, argument, or environment. The harness takes no target argument at all.
  This is not, and must never become, a general-purpose load or stress tool.
- The harness's concurrency, round count, and the **amount of work a round may name** are all bounded
  by explicit configured maxima.
- Every service declares an explicit memory and CPU limit, so the whole resource envelope is bounded
  and legible on the host.
- Containers run non-root with all capabilities dropped, `no-new-privileges`, and a read-only root
  filesystem.
- The database's data directory lives on tmpfs. No run inherits another run's state, and nothing
  persists on the host.
- The compressed import fixture is generated at run time from repetitive fictional records at a
  documented, modest, single-layer ratio. No such artifact is committed, and nothing here is nested,
  recursive, or self-referential.

## Not what this is

This demonstration is about an application admitting unbounded work through its **documented
interface**. It is not about network- or transport-layer denial of service, and it makes no
throughput, latency, or capacity claim of any kind. Concurrency exists here to expose an unbounded
code path, never to measure a system.
