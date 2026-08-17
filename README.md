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

## Running it

Requires **Docker** and nothing else — no PostgreSQL, no Python environment, no host tuning.

```sh
bash scripts/demo.sh      # the sequential demonstration
bash scripts/verify.sh    # the complete boundary: demo, audit gate, containment, ruff, mypy, tests
```

A documented run parameter selects how many replicas are addressed:

```sh
LIMITLESS_REPLICAS=1 bash scripts/demo.sh
```

## Safety

- The network is `internal: true`. There is **no egress**, and no service publishes a port.
- The demonstration's targets are its own in-network services and **cannot be redirected** at any
  other host by configuration, argument, or environment. This is not, and must never become, a
  general-purpose load or stress tool.
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
