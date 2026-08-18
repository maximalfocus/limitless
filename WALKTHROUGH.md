# limitless — a walkthrough

> **This repository contains a deliberately vulnerable service.** It is local educational material.
> It must never be deployed, exposed, or run anywhere but a developer's own machine. It does not
> start without two deliberate actions. Every tenant, company, provider, price, budget and credential
> here is fictional, the network has no egress, and the load harness cannot be pointed at anything
> but the demonstration's own services.

---

## 1. The idea in one line

**A request names work, and the request's own size tells you nothing about how much.**

Eighteen bytes of query string can name fifty thousand records. A hundred and sixty-nine kilobytes of
gzip can name seven hundred and twenty thousand. One ordinary-looking `POST` can name fifty thousand
metered lookups at a provider that bills for every one of them.

The gap between what a request costs *the caller* and what it costs *you* is the vulnerability. The
**ratio** between those two numbers is the thing a fix has to change — and it is the number this
whole demonstration is built to show you.

## 2. The five dimensions

A caller can drive five different quantities without bound. A service is only bounded if it bounds
all five, because an attacker only needs one.

| Dimension | The question nobody asked | What it looks like here |
|---|---|---|
| **how big** | how many bytes is this body? | a body read whole, however large it turns out to be |
| **how many** | how many items does it name? | a batch of 50 000, a page size of 1 000 000 |
| **how much it becomes** | what does it expand to? | 169 KB of gzip becoming 50 MB of records |
| **how often** | how much may this caller spend? | one undivided pool with no per-tenant partition |
| **how many at once** | how much work is in flight? | no cap, no deadline, and every connection occupied |

### Amplification is a ratio

The demonstration reports one number above all others: **fictional cents admitted per byte of input
the caller supplied**. Run `compare` and read the last two columns:

```
secure scenarios     : worst amplification 0.0199 cents per input byte
unbounded scenarios  : worst amplification 17.0672 cents per input byte
the ratio between them: 856x
```

That is the whole argument. Not "the unbounded one is slower" — it is 856 times more efficient at
converting the attacker's bytes into your money.

## 3. The names for this

The primary anchor is **API4:2023 Unrestricted Resource Consumption**, together with:

| Identifier | What it names |
|---|---|
| `CWE-770` | allocation of resources without limits or throttling |
| `CWE-400` | uncontrolled resource consumption |
| `CWE-799` | improper control of interaction frequency |
| `CWE-405` | asymmetric resource consumption (amplification) |
| `CWE-409` | improper handling of highly compressed data |
| `CWE-789` | memory allocation with an excessive size value |

Other names for the same thing: *missing rate limiting*, *resource exhaustion*, *application-layer
denial of service*, *denial of wallet*.

### A caveat that matters

**API4:2023 is the primary, published anchor.** The OWASP API Security Top 10 (2023) entry for API4
itself references `CWE-770`, `CWE-400` and `CWE-799`. Nothing about that anchor is a judgement call.

**A04:2021 Insecure Design is named here as a *partial* secondary anchor**, and it rests on
**`CWE-799` alone**, which *is* a published member of the A04:2021 CWE mapping. This demonstration
claims no more than that. `CWE-770`, `CWE-400`, `CWE-405`, `CWE-409` and `CWE-789` appear in **no**
2021 Top-10 category, so the amplification and allocation shapes are carried by their CWE identifiers
alone and by API4:2023. That distinction is stated in these terms rather than implying whole-demo
coverage of A04:2021.

**LLM10 Unbounded Consumption** is the same family expressed in a model-serving context — token
spend, inference cost, model denial of service. It is named here as a relative and is **not**
claimed; it is a different demonstration.

## 4. The ladder: four shapes, and the repair that fails against each

### Shape 1 — the client names the work

`CWE-405`, `CWE-789`. One `POST /v1/enrich` naming **50 000** records is accepted and performed:
**200 000 fictional cents**, four fifths of the entire monthly cap, from a single request. The same
mistake on a second sink: `GET /v1/records?page_size=1000000` — **eighteen bytes** of query string —
serializes **50 400** records.

> **The repair that fails: a request-count rate limit.**
> Sixty requests a minute per tenant, genuinely enforced. Proved real on one tenant, which was
> refused on request 61 of 61. Then a caller stayed entirely inside it and drained the **whole**
> 250 000-cent cap on 62 440 lookups, while the limiter reported **zero** violations against it — and
> was telling the truth.
>
> **The limit counted requests. The resource is measured in lookups.** The unit of the limit must be
> the unit of the resource.

### Shape 2 — unbounded repetition against an un-partitioned budget

`CWE-799`, `CWE-770`. No per-tenant quota; every tenant's spending drawn from one undivided pool. An
ordinary-looking burst from `TEN-ORCHID` drains the shared cap to exactly zero, after which
`TEN-BASIL` and `TEN-WREN` — who spent **nothing** — are refused.

> **The repair that fails: the limiter's scope, and its key.**
> An in-process counter is added. Addressed at **one** replica it holds exactly: 40 000 cents, right
> to the cent. Addressed at **two**, with nothing else changed at all, the effective allowance is
> **80 000**.
>
> Nothing was corrupted and nothing was lost. The budget was simply enforced **twice, in parallel**,
> because there are now two counters and each is enforcing the whole allowance by itself. A limiter's
> counter must be shared by every process that serves the endpoint.
>
> Keyed on a **caller-supplied** identifier instead of the authenticated tenant, the same limiter is
> defeated more cheaply still: change one header, get a fresh allowance. The key must be the
> server-derived authenticated principal.

### Shape 3 — expansion, checked in the wrong place on the wrong number

`CWE-409`. A **168 745-byte** gzip bundle expands to about **50 MB** of NDJSON and admits **720 000**
records — **2 880 000 fictional cents**, **11.5 times** the entire monthly cap, from an upload
smaller than a photograph. The unbounded path decompresses to completion before counting anything.

> **The repair that fails: "we do check the size."**
> The check exists and is honoured — it refused a 281 KB upload. It is wrong twice over.
>
> It measures the **compressed** size, a number whose relationship to the real one the *submitter*
> chose. And it runs **after** the whole body has been buffered into memory, so the allocation it
> exists to prevent has already happened. Even when it refuses, it refuses too late to have helped.

### Shape 4 — unbounded in-flight work and no deadline

`CWE-770`. With the provider held, a burst occupies every worker: eight calls in flight against a
replica with eight database connections, each request keeping one for the whole duration of its
upstream call. `GET /v1/jobs/{job_id}` — which touches **no provider, no budget and no expensive
path** — stops being served.

**One unbounded endpoint took down the endpoints that were fine.** The endpoint that failed contains
no defect of its own.

> **The repair that fails: a deadline that returns but does not cancel.**
> The caller now receives a timeout at one second. The upstream call in flight went from 0 to **1**
> and stayed there; when the hold was released, the abandoned call completed and billed anyway.
>
> The **response** was bounded. The **work** was not. A deadline is only a deadline if it cancels.

## 5. What this flaw is not

Three negative controls, so nobody leaves with the wrong repair in mind.

### Every request is authenticated and authorized

`TEN-ORCHID` is an ordinary paying customer in good standing. Every request in every shape above uses
a documented endpoint, a valid credential, and touches only its own data. **No object-level,
function-level or property-level authorization control would refuse a single one of them.** This is
the boundary between this flaw and the access-control demonstrations: authentication is working
perfectly here, and it is beside the point.

### One of each request is perfectly correct

Each shape, issued once at an ordinary size, returns a correct, complete, prompt response. A
functional suite written against those four requests is **entirely green**.

**That is the reason the defect ships.** The defect exists only in the **aggregate**, and the
aggregate is what functional suites do not assert on.

### More capacity is not a fix

The same drain, at three settings:

| Setting | Time-to-drain | Work admitted | Amplification |
|---|---|---|---|
| baseline | 32 requests | 62 000 lookups | 0.0945 |
| capacity doubled | **63** requests | 124 000 lookups | 0.0957 |
| request rate halved | **63** requests | 62 000 lookups | 0.0961 |

Time-to-drain doubled. **The amplification ratio moved by 0.0015.** Added capacity buys a constant
factor; the fix has to change the structure.

## 6. The fix: five bounds, each at the edge

| Control | What it does | The trade-off |
|---|---|---|
| **bounds while reading** | counts bytes as they arrive and stops at the maximum, so an over-large body is refused *without ever being allocated* | a legitimate large upload needs a deliberate, documented ceiling rather than an accident |
| **item and page bounds** | a batch length and a caller-supplied page size, **refused** rather than silently clamped | callers must paginate; silently serving something smaller hides the bound from them and from you |
| **expansion bounds** | an absolute decompressed-byte ceiling **and** a ratio, enforced *during* decompression, aborting mid-stream | a legitimate highly-compressible import needs its ratio raised deliberately |
| **a cost-based quota** | charged in **provider lookups**, keyed on the server-derived authenticated tenant, reserved *before* the work by one atomic conditional write decided on affected row count | the allowance must live in shared state, which means a round trip on the hot path |
| **bounded concurrency and cancelling deadlines** | an explicit in-flight cap with excess **shed** rather than queued, and deadlines that **cancel** | the cap must sit above ordinary concurrency, or it refuses valid work — which is the failure mode the fix must not have |

### Why each belongs at the edge

Every one of these runs **before** the thing it protects is allocated. That is not a stylistic
preference — it is the difference between a control and a report. A size check after the body is
buffered, a quota checked after the provider is called, a deadline that fires after the work is done:
each of those tells you what happened. None of them stops it.

The order on every path that costs money is always:

```
bound the input  →  reserve the money  →  take a slot  →  do the work  →  settle
```

...and never any other, so a request that is going to be refused is refused at the cheapest possible
moment.

### Refusals give nothing away

Every over-limit input — body, batch, page size, decompressed bytes, expansion ratio — is the same
`413` with the same body. An exhausted allowance is a `429` with a **fixed** `Retry-After` that is not
the real reset instant. Exactly one generic audit event is emitted per refusal, naming neither the
bound that refused nor any quantity at all. A caller who could tell *which* limit refused them, and
by how much, could map every bound in a handful of probes.

### One more thing the fix must not do

A heavy but legitimate tenant must be able to spend its **whole** allowance without being refused. In
the harness that is an exact assertion: the spending tenant finishes 60 cents short of its 40 000-cent
partition — less than one more request — with **zero** capacity refusals. A control that protects the
budget by turning away paying customers has not fixed anything.

## 7. Reproducing it

Two modes, and the difference between them is the point.

**Deterministic** (the default) uses the provider fixture's hold/release control to make occupancy
and exhaustion **arithmetic rather than a race with the clock**: a configured number of held calls
occupies a configured number of slots, and the consequence is observed at a known instant.

> **The instrumentation changes only *when* work is released. It never changes *whether* the
> application bounded it.** It lives in the provider fixture and in unbounded code paths only, never
> in a secure one.

**Natural** has **no instrumentation whatsoever** in any code path and no provider hold. It is the
evidence for the claim above: the same defects appear without it. A natural-mode run that observes no
violation is reported as **inconclusive** — never as a pass.

Every required assertion in this project is an assertion about **counted accounting** — bytes, items,
lookups, fictional cents, occupied slots, refusals. **None of them depends on wall-clock latency,
elapsed time, or how fast the host is.** A result that did would not be a result.

```sh
bash scripts/demo.sh      # the sequential demonstration
bash scripts/verify.sh    # everything: demo, harness, ladder, repairs, controls, tests
docker compose run --rm compare              # every scenario in one table
docker compose run --rm compare --verbose    # ...and the per-request records behind it
```

## 8. Deliberately not built

Named here so you know where the edges are, and why each is a different lesson:

- **Network- and transport-layer denial of service** — packet floods, connection floods, reflection.
  This demonstration is about an application admitting unbounded work through its **documented
  interface**, and nothing else.
- **Performance work of any kind** — no throughput, no latency, no benchmarking, no capacity
  planning. Concurrency exists here to expose an unbounded code path, never to measure a system. **This project makes no
  performance claim.**
- **Algorithmic-complexity attacks** (`CWE-1333` regular-expression denial of service, quadratic
  parsing, hash-collision flooding) — there the flaw is *in* the algorithm rather than in the absence
  of a limit, and it teaches a different repair.
- **Parser-expansion attacks other than the single documented gzip bundle** — nested, recursive or
  self-referential archives, and XML entity expansion (`CWE-776`). The fixture here is one layer of
  ordinary gzip, generated at build time from repetitive fictional records at a documented ratio, and
  nothing in this repository explains how to build anything else.
- **Business-flow abuse** (`API6:2023`), anti-automation, and scraping economics — a neighbouring
  roadmap row, a different demonstration.
- **Distributed rate-limiting and traffic-management infrastructure** — a limiter service, an API
  gateway, a service mesh, a WAF, edge rate limiting. That is the common production answer, and it is
  deliberately not built here: the quota is implemented in this project's own shared store so the
  control stays readable as application code.
- **Circuit breakers, retry budgets, and backpressure design** as subjects in their own right.

## 9. Neighbouring demonstrations

- The **concurrent load harness** form, the **two-mode reproduction contract**, and the **atomic
  conditional write** are inherited from the check-then-act demonstration, where the race itself is
  the subject. Here the atomic reservation is used as a **known-correct tool**, not as the lesson.
  The neighbouring failure there is worth contrasting with shape 2: a lost update *corrupts* state,
  whereas an in-process limiter loses nothing at all — it simply enforces the budget N times over.
- A **client-side page-size cap is not a server-side bound**, which the object-access demonstration
  makes in its own context. Shape 1's second sink is the same lesson from the other end.
- **LLM10 Unbounded Consumption** is this same family in a model-serving context.

---

> Once more, because it is the most important line in this file: **the vulnerable service in this
> repository is local educational material and must never be deployed.** It requires two deliberate
> actions to start, publishes no port, has no egress, and bills only fictional money at a fictional
> provider this demonstration operates itself.
