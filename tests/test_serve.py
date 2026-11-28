"""The JSON API: every command that has something to say, over HTTP.

The server had `/search` and `/ask` and nothing else, so the extraction layer,
the entity groups and the three tools were CLI-only — which means a client in
another language could not reach them at all. These tests are about the routes
existing and returning what the command returns, not about HTTP.
"""
import json
import os
import sys
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.harvest.fetch import Document
from vrusha.ingest import ingest_documents
from vrusha.languages import load_registry
from vrusha.rules import load_rules
from vrusha.store import entities, records
from vrusha.store.db import Store
from vrusha.store.embed import HashEmbedder


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    ensure_dirs(cfg)
    return cfg


def _table(header, rows):
    head = "".join(f"<th>{cell}</th>" for cell in header)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
                   for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


@pytest.fixture()
def server(tmp_path):
    from vrusha.serve import serve_in_thread

    cfg = _cfg(tmp_path)
    store = Store(resolve(cfg, "db"))
    document = Document(url="https://a.test/plans", title="plans", status=200,
                        text="Pricing plans and their monthly cost. " * 30,
                        html=_table(["plan", "price"],
                                    [["starter", "Rs 499"], ["team", "Rs 1499"]]))
    ingest_documents([document], store, cfg, load_rules(), load_registry(),
                     HashEmbedder(64), licence="MIT", verbose=False)
    for index, html in enumerate([
            _table(["vendor", "price"], [["Acme Corp", "Rs 100"],
                                         ["Beta Ltd", "Rs 200"]]),
            _table(["vendor", "uptime"], [["ACME Corporation", "99.9%"],
                                          ["Beta Ltd", "99.5%"]])]):
        url = f"https://a.test/{index}"
        doc = store.add_document(url=url, title=url, text="body " * 120)
        records.extract_document(store, doc, html, url)
    entities.build(store)
    store.commit()
    store.close()

    httpd, _thread = serve_in_thread(cfg, HashEmbedder(64), port=0)
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return json.loads(response.read())


def post(base, path, payload):
    request = urllib.request.Request(
        base + path, method="POST", data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_the_root_lists_every_route(server):
    """A JSON API nobody can discover is a JSON API nobody uses."""
    index = get(server, "/")
    assert index["service"] == "vrusha"
    for route in ("POST /search", "POST /ask", "GET /tables", "GET /entities",
                  "GET /tools", "POST /math"):
        assert route in index["routes"]
    assert "research" in index["not_here"]


def test_tables_and_one_table(server):
    listed = get(server, "/tables")["tables"]
    assert listed, "the fixture page has a table"
    table_id = listed[0]["table_id"]
    one = get(server, f"/tables/{table_id}")
    assert one["table_id"] == table_id
    assert one["rows"]


def test_an_unknown_table_is_a_404(server):
    with pytest.raises(urllib.error.HTTPError) as error:
        get(server, "/tables/999999")
    assert error.value.code == 404


def test_a_table_id_that_is_not_a_number_is_a_400(server):
    with pytest.raises(urllib.error.HTTPError) as error:
        get(server, "/tables/abc")
    assert error.value.code == 400


def test_entities_are_served_with_their_scores(server):
    payload = get(server, "/entities")
    assert payload["entities"]
    group = payload["entities"][0]
    assert group["canonical"] and group["aliases"]
    assert all("score" in alias for alias in group["aliases"])


def test_tools_report_what_this_machine_can_run(server):
    tools = get(server, "/tools")["tools"]
    assert {item["name"] for item in tools} == {"dubforge", "forge3d", "cutforge"}
    assert all("reason" in item for item in tools)


def test_math_says_so_when_nothing_matches(server):
    payload = post(server, "/math", {"expression": "e^{i\\pi}+1=0"})
    assert payload["expression"]
    assert isinstance(payload["matches"], list)


def test_math_refuses_an_empty_expression(server):
    with pytest.raises(urllib.error.HTTPError) as error:
        post(server, "/math", {"expression": "  "})
    assert error.value.code == 400


def test_search_and_ask_still_work(server):
    """The routes that existed must not regress when the index is added."""
    answer = post(server, "/search", {"text": "pricing plans", "k": 3})
    assert answer["backend"] and answer["hits"]
    asked = post(server, "/ask", {"question": "what do the plans cost", "k": 3})
    assert asked["question"]
