# Security policy

This project contains a vulnerability **on purpose**. That makes "is this a security issue?" an
unusually confusing question here, so this file exists to answer it.

## The unbounded consumption in this repository is the subject, not a bug

`limitless` demonstrates **unrestricted resource consumption** — OWASP API4:2023, and `CWE-770`,
`CWE-400`, `CWE-799`, `CWE-405`, `CWE-409`, and `CWE-789` — by shipping code that has it. The
following are all deliberate, documented, and under test:

- the shape where **the client names the work**, so one request names 50 000 records and bills
  0.80x the whole fictional monthly cap, and 18 bytes of query string serialize 50 400 records;
- **repetition against an un-partitioned budget**, where one tenant drains the shared pool to zero
  and tenants that spent nothing are refused;
- **expansion with the size check in the wrong place on the wrong number**, where 169 KB of gzip
  admits work worth 11.5x the entire fictional cap;
- **unbounded in-flight work with no cancelling deadline**, where held calls occupy every connection
  until an endpoint that needs no provider stops being served;
- the four **half-fixes** — a request-count rate limit, an in-process allowance, a caller-keyed
  allowance, and a deadline that returns without cancelling — each genuinely honoured and each
  defeated anyway; and
- the **instrumented hold/release control** in the provider fixture that makes the in-flight shape
  arithmetic rather than a race with the clock.

Every one of these lives behind two deliberate opt-in actions — the `vulnerable` Compose profile and
`ALLOW_VULNERABLE_DEMO=true` — and is explained in [`WALKTHROUGH.md`](WALKTHROUGH.md). **Please do
not report any of them.** They are the demonstration. A report that the vulnerable application is
vulnerable is the project working as designed.

## What *is* worth reporting

An **unintended** weakness — one that is not part of the lesson. For example:

- a flaw in the secure application's bounds, quota, cap, or deadlines, or in the harness,
  comparison, or test code;
- a container or Compose misconfiguration that widens the blast radius beyond the demo's own
  services — an unintended published port, a lost capability drop, a way out of the egress-less
  network, or a missing memory or CPU limit;
- a way for the vulnerable application to start without **both** opt-in actions;
- any way to point the harness at a host that is not one of this demonstration's own services, which
  would turn a teaching tool into a load-generating one;
- a real credential, personal datum, or non-fictional identifier anywhere in the repository, its
  history, or a run artifact; or
- a supply-chain problem in the pinned dependencies or base images.

### How to report

Use **GitHub private vulnerability reporting** on this repository:

> **Security** tab → **Report a vulnerability**

That opens a private advisory visible only to the maintainer. Please do not open a public issue for
an unintended weakness, and please include the commit you observed it on plus the smallest
reproduction you have.

There is no security contact email; the private advisory is the reporting path.

## No supported versions, and nothing to report against

There is no supported-version table here, because there are no versions to support.

This project is **local educational material**. It is not deployed, not hosted, and not published as
a package or container image. There is no running system, no endpoint, and no user data anywhere —
so there is nothing to compromise operationally, and reports about a hosted `limitless` are
necessarily about something that is not this project.

The project makes **no** service-level, support-duration, compatibility, or production-readiness
promise of any kind. Nothing here is intended for production use, and the vulnerable application must
never be deployed anywhere.
