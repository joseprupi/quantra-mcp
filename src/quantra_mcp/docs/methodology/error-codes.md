# Error codes and what they mean

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.7.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

400 = the request is wrong (missing field, bad date, unsupported combination); 422 = well-formed but unpriceable (a QuantLib-level failure); 404 = a referenced id is not in the request's pricing block. Retrying without changing the payload never helps. The `error` field carries the cause.

## What the engine does

### 1. Request rules: JSON content type, non-empty body, ISO dates, omitted is not defaulted, presence selects a variant.

````text
## Request rules

- **`Content-Type: application/json` is required** on every POST. Anything else
  is `415`.
- **The body must be non-empty** and at most **10 MiB**. An empty or
  whitespace-only body is `400`; an oversized one is `413`.
- **Dates are ISO-8601 `YYYY-MM-DD`, exactly.** Slash formats and impossible
  dates (`2024-02-30`) are rejected with
  `Invalid date '<value>': expected YYYY-MM-DD`. Response dates are ISO too.
- **Omitted is not defaulted.** A field the product needs but the request does
  not carry is an error naming the field (`Schedule.calendar is required`) —
  never a silent zero, and never a silently chosen convention. This covers
  schedule and leg conventions, day counters, curve-helper quotes, product
  discriminators (`fra_type`, `cap_floor_type`, CDS `side`), and volatility
  specs. See `versioning.md` for the full list introduced in 0.2.0.
- **Presence, not sentinels, selects a variant.** Where several quote forms are
  accepted (`rate` / `price` / `spread` / `quote_id`), supply exactly the one
  you mean; supplying none is an error, and a genuine `0` is representable.
````

Source: `docs/http-api.md@v0.7.0:L7-L24`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/http-api.md#L7-L24>

### 2. Status code table.

````text
## Status codes

The gateway maps the engine's gRPC status onto HTTP:

| HTTP | When |
| --- | --- |
| `200` | Priced. List endpoints may still carry per-item errors in the body. |
| `400` | Invalid argument: missing required field, malformed date, or an unsupported combination of otherwise-valid fields. |
| `401` / `403` | Reserved; the shipped server does not authenticate. |
| `404` | Unknown route, or a referenced id (curve, model, volatility, credit curve, index, quote) that is not present in the request's `pricing` block. |
| `409` | Reserved. |
| `413` | Body over the 10 MiB cap, or a gRPC message over the transport cap. |
| `415` | `Content-Type` is not `application/json`. |
| `422` | Well-formed request the pricing engine could not evaluate — a QuantLib-level failure such as a degenerate schedule or an unbootstrappable curve. |
| `429` | Resource exhausted for a reason other than payload size. |
| `500` | Unexpected server fault. |
| `501` | A field or combination that is on the wire but not implemented. |
| `503` | No worker available. |
| `504` | The request's deadline or server-side budget expired (see `QUANTRA_REQUEST_BUDGET_MS` in `configuration.md`). |

The distinction that matters most: **`400` means the request is wrong, `422`
means the request is well-formed but unpriceable.** Retrying either without
changing the payload will not help.
````

Source: `docs/http-api.md@v0.7.0:L26-L48`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/http-api.md#L26-L48>

### 3. Error body: `error` is the field to read; `code` / `code_name` are the gRPC status; `message` repeats `error`.

````text
## Error body

Every non-2xx response is a JSON object:

```json
{
  "error": "Schedule.calendar is required",
  "code": 3,
  "code_name": "INVALID_ARGUMENT",
  "message": "Schedule.calendar is required"
}
```

`error` carries the real cause and is the field to read. `code` /
`code_name` are the underlying gRPC status, kept for compatibility;
`message` repeats `error`.
````

Source: `docs/http-api.md@v0.7.0:L50-L65`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/http-api.md#L50-L65>

### 4. Which omissions are 400s (0.5.0 list).

````text
### Every request value and convention must be explicit

- **Values** (were silent zeros): notionals, `base_nominal`, `face_amount`,
  `redemption`, strikes and digital `cash`, `QuoteSpec.value`, `coupon_rate`,
  optionlet `volatility`, inflation helper quotes, and `CdsQuote.quote_type`.
  Amounts must be positive; rates may be negative but must be present and finite.
- **Conventions** (were hard defaults): calendars, day counters, frequencies,
  business-day conventions, settlement/fixing days, `recovery_rate`,
  `end_of_month`, `Period` unit/count, curve interpolator, and the CDS ISDA
  accrual flags, across the index, swap-index, curve-helper, inflation and
  credit-curve specs. Omission → 400 naming the field.
- **Duplicate ids** in a curves / indices / vol-surfaces / models /
  credit-curves list are rejected (were silently first-wins).
````

Source: `docs/versioning.md@v0.7.0:L159-L171`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/versioning.md#L159-L171>

### 5. Headers: `X-Quantra-Api-Version` on every POST response; `X-Request-Id` echoed when sent.

````text
## Headers

| Header | Direction | Meaning |
| --- | --- | --- |
| `X-Quantra-Api-Version` | response | The served API version, from the `VERSION` file. Matches OpenAPI `info.version` and `GET /meta`. Present on every POST response, success or failure. |
| `X-Request-Id` | request → response | If you send one, it is sanitized (printable non-space ASCII, capped at 128 characters), forwarded to the engine as gRPC metadata, tagged onto every engine log line for that request, and echoed back. If you do not send one, none is echoed. |

Send an `X-Request-Id` on anything you may need to trace: it is the only way to
correlate a client-side failure with the engine log lines that produced it.
````

Source: `docs/http-api.md@v0.7.0:L67-L75`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/http-api.md#L67-L75>

## Not documented in engine v0.7.0

- Nothing further: the excerpts above cover the behaviour the server relies on.
