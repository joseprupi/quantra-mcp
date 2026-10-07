# Example engine requests

Live-verified request bodies served as `quantra://examples/{name}` resources.

| Name | Endpoint | Source |
|---|---|---|
| `sofr-bootstrap-request` | `POST /bootstrap-curves` | https://quantra.io/blog/bootstrapping-a-sofr-curve (downloadable request JSON, `/blog/data/sofr-bootstrap-request.json`) |
| `sofr-ois-swap-request` | `POST /price-ois-swap` | same post, `/blog/data/sofr-ois-swap-request.json` |

Both come from the Quantra blog post *Bootstrapping a SOFR curve* (2026-08-03),
where they were run verbatim against the public engine (`https://api.quantra.io`,
engine 0.6.0) and checked against QuantLib-python 1.41: the OIS swap prices to
NPV `337986.7913...` and the bootstrap's 50Y discount factor is
`0.262755579831`. They are copied byte-for-byte; do not edit them here.
