"""The concurrent load and amplification harness.

It generates genuine concurrent load against the demonstration's own in-network services, records
every request, and reconciles the result into an accounting whose central number is the
**amplification ratio** — fictional cents admitted per byte of input.

Two properties are requirements rather than defaults, and both are enforced in code rather than
documented as intentions: the harness accepts **no arbitrary target**, and its load is bounded by
explicit configured maxima. It measures admitted work and charged cost only, and makes no
throughput, latency, or capacity claim of any kind.
"""
