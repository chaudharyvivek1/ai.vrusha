# The JSON API

```bash
vrusha serve --host 127.0.0.1 --port 8000
curl -s localhost:8000/ | jq .
```

The server is **Python standard library** — `http.server.ThreadingHTTPServer`,
no framework, no Node, no build step. There is no JavaScript or TypeScript in this
repository at all; the API is JSON over HTTP so that a client in any language can
use it, and `curl` is the reference client.

## Routes

`GET /` returns the index below, because an API nobody can discover is an API
nobody uses.

| route | body | returns |
|---|---|---|
| `GET /` | — | the route index |
| `GET /health` | — | `{"ok": true}` |
| `GET /stats` | — | vectors, dimension, every backend's state |
| `GET /backends` | — | each vector engine, ready or not, and why |
| `GET /tables` | — | extracted tables: id, name, kind, url, row count |
| `GET /tables/<id>` | — | one table, with its rows |
| `GET /entities` | — | resolved entity groups, each alias with its score |
| `GET /tools` | — | dubforge, forge3d, cutforge: ready or not, and why |
| `POST /search` | `{text \| vector, k, filter}` | ranked chunk ids and the backend that answered |
| `POST /ask` | `{question, k}` | the answer, with citations and the chunks used |
| `POST /math` | `{expression}` | stored formulae that match, with sources |

```bash
curl -s -X POST localhost:8000/search \
  -H 'content-type: application/json' \
  -d '{"text": "how do I train the model", "k": 3}' | jq .

curl -s localhost:8000/tables | jq '.tables[0]'
curl -s -X POST localhost:8000/math \
  -H 'content-type: application/json' \
  -d '{"expression": "b^2-4ac"}' | jq .
```

## What is deliberately not a route

`research` fetches the web, so it is a command and not a request: a client that
holds a socket open for a page fetch, an extraction and an answer has been given
a timeout problem, not a feature. Run it as a command and read the JSON
(`vrusha --json research "..."`). The index says so in `not_here`, so nobody has
to guess.

## Threading, and why the store is opened here

`serve()` opens its own `Store` with `check_same_thread=False` rather than
accepting one. A threaded server handed a connection made in another thread fails
every request with a `ProgrammingError`, and the failure reads as "no backend
available" rather than "wrong connection" — a bug that costs an hour.

## What this is not

No authentication, no rate limiting, no TLS, no CORS. It binds `127.0.0.1` by
default and is meant to sit behind a proxy or on a loopback interface. If you
expose it, put something in front of it.
