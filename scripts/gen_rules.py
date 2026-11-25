"""Generate the ten level folders under vrusha/rules/levels/.

Run once: python scripts/gen_rules.py
Kept in the repo so the rule set is auditable and regenerable.
"""
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vrusha", "rules", "levels")

LEVELS = {
    1: ("inviolable", [
        ("01-licence.json", [
            dict(id="licence.allowed", action="allow",
                 value=["MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC",
                        "MPL-2.0", "Unlicense", "CC0-1.0", "0BSD", "CC-BY-4.0"],
                 rationale="Only these licences permit storing the material and training on it. Anything else is refused, not guessed at."),
            dict(id="licence.blocked", action="block",
                 value=["AGPL-3.0", "GPL-3.0", "GPL-2.0", "LGPL-3.0", "LGPL-2.1",
                        "SSPL-1.0", "BUSL-1.1", "proprietary", "unknown"],
                 rationale="Copyleft and unknown licences either infect the output or cannot be verified. Unknown is a refusal, not a benefit of the doubt."),
            dict(id="licence.require_file", action="block", value=True,
                 rationale="A repository without a licence file has no licence to grant. Refuse it."),
        ]),
        ("02-robots.json", [
            dict(id="robots.respect", action="block", value=True,
                 rationale="robots.txt is checked before every request, not after. A disallowed path is never fetched."),
            dict(id="robots.fail_closed", action="block", value=True,
                 rationale="If robots.txt cannot be read, treat the site as disallowed. Failing open is how crawlers get blocked."),
        ]),
        ("03-secrets.json", [
            dict(id="secrets.scan", action="block",
                 value={"enabled": True, "entropy_threshold": 4.6, "min_token_len": 20,
                        "patterns": ["AKIA[0-9A-Z]{16}", "ghp_[A-Za-z0-9]{36}",
                                     "sk-[A-Za-z0-9]{20,}", "-----BEGIN [A-Z ]*PRIVATE KEY-----",
                                     "xox[baprs]-[A-Za-z0-9-]{10,}"]},
                 rationale="A key in the training corpus is a key in the model. High-entropy tokens and known key shapes are stripped before anything is stored."),
            dict(id="secrets.redact_not_store", action="block", value=True,
                 rationale="Redaction happens on the raw bytes. The secret never reaches the database, not even in a rejected row."),
        ]),
        ("04-policy.json", [
            dict(id="policy.refused_domains", action="block",
                 value=["stackoverflow.com", "stackexchange.com", "geeksforgeeks.org",
                        "leetcode.com", "hackerrank.com", "interviewbit.com",
                        "tutorialspoint.com", "javatpoint.com", "w3schools.com"],
                 rationale="These sites forbid crawling or reuse in their terms. Refused by name so no configuration can quietly re-enable them."),
            dict(id="policy.terms_review", action="warn", value=True,
                 rationale="A domain not on either list is a judgement call. Warn, record the decision in the manifest, and let the operator decide."),
            dict(id="policy.personal_data", action="block", value=True,
                 rationale="Names, phone numbers, addresses and identifiers of private people are not training data."),
            dict(id="policy.safety", action="block",
                 value=["weapons", "exploitation", "malware", "csam"],
                 rationale="No volume of data justifies these. Refused at the gate, not filtered later."),
        ]),
    ]),
    2: ("legal", [
        ("01-attribution.json", [
            dict(id="attribution.required", action="warn", value=True,
                 rationale="Every stored unit keeps its URL and retrieval date so any output can name its source."),
            dict(id="attribution.template", action="set",
                 value="Source: {title} <{url}> (retrieved {retrieved})",
                 rationale="One template, used by the composer and the report, so citations look the same everywhere."),
            dict(id="attribution.keep_copyright_notice", action="block", value=True,
                 rationale="A copyright header inside a source file is part of the file. Stripping it would remove the notice the licence requires."),
        ]),
        ("02-copyright.json", [
            dict(id="copyright.max_quote_chars", action="warn", value=2000,
                 rationale="Quoting a whole article into an answer is republication. Long passages are summarised, not reproduced."),
            dict(id="copyright.store_full_text", action="allow", value="private_only",
                 rationale="Full text is stored locally for retrieval. It is never republished; the store is a private index, not a library."),
        ]),
    ]),
    3: ("integrity", [
        ("01-provenance.json", [
            dict(id="provenance.required", action="block", value=True,
                 rationale="A unit with no source URL cannot be checked and cannot be cited. It does not enter the store."),
            dict(id="provenance.fields", action="block",
                 value=["url", "retrieved", "licence", "content_hash"],
                 rationale="These four fields are the minimum that makes a row auditable a year later."),
            dict(id="provenance.content_hash", action="set", value="sha256",
                 rationale="The hash is how a re-harvest proves the page did not change, and how the duplicate gate finds exact copies."),
        ]),
        ("02-truth.json", [
            dict(id="no_fabrication", action="block", value=True,
                 rationale="If retrieval returns nothing above the refusal threshold, the answer is 'I do not have this', never an invented one."),
            dict(id="timestamp.utc", action="set", value=True,
                 rationale="All timestamps are UTC ISO-8601 so a store can be merged with another without a timezone argument."),
        ]),
    ]),
    4: ("language", [
        ("01-encoding.json", [
            dict(id="encoding.required", action="block", value="utf-8",
                 rationale="One encoding everywhere. Undecodable bytes are dropped, not mojibaked into the corpus."),
            dict(id="text.normalize_unicode", action="set", value="NFC",
                 rationale="Two spellings of the same character must hash the same, or deduplication silently fails."),
        ]),
        ("02-detection.json", [
            dict(id="language.detect_min_confidence", action="warn", value=0.55,
                 rationale="Below this the text is probably mixed or too short. It is flagged so you can look, not silently dropped."),
            dict(id="language.allowed", action="allow",
                 value=["en", "hi", "mr", "ta", "bn"],
                 rationale="The natural languages this build carries profiles for. Adding one is a profile file, not a code change."),
            dict(id="language.code_allowed", action="allow",
                 value=["python", "javascript", "typescript", "java", "c", "cpp", "go",
                        "rust", "sql", "shell", "html", "css", "json", "yaml", "markdown"],
                 rationale="Programming languages with a chunking profile. A file outside this list is stored as plain text."),
        ]),
    ]),
    5: ("structure", [
        ("01-address.json", [
            dict(id="address.segments", action="set", value=5,
                 rationale="domain.family.kind.cluster.ordinal. Changing this is a schema change and breaks every stored address."),
            dict(id="address.weights", action="set", value=[8.0, 4.0, 2.0, 1.0, 0.5],
                 rationale="Coarse segments must dominate the distance, or 'far apart' stops meaning 'different'."),
            dict(id="address.prefix_is_folder", action="set", value=True,
                 rationale="A prefix of an address is a real folder: everything under 0.5 is function material. This is what makes browsing possible."),
        ]),
        ("02-chunk.json", [
            dict(id="chunk.max_lines", action="set", value=60,
                 rationale="One function or one section per chunk. Longer units retrieve worse because the vector averages two topics."),
            dict(id="chunk.overlap_lines", action="set", value=3,
                 rationale="A little overlap so a function split at the boundary is still findable from either side."),
            dict(id="chunk.min_chars", action="set", value=80,
                 rationale="Shorter than this is a fragment, not a unit of meaning."),
            dict(id="schema.version", action="set", value=1,
                 rationale="Bump this when the tables change, so an old database is refused instead of half-read."),
        ]),
    ]),
    6: ("quality", [
        ("01-thresholds.json", [
            dict(id="quality.min_words", action="set", value=120,
                 rationale="Under this a page is usually a stub, a 404 or a cookie wall."),
            dict(id="quality.min_text_ratio", action="set", value=0.45,
                 rationale="Visible text divided by raw bytes. Below this the page is markup and navigation, not content."),
            dict(id="quality.max_link_density", action="set", value=0.30,
                 rationale="A page that is mostly links is an index page. It belongs in the frontier, not in the corpus."),
            dict(id="quality.boilerplate_strip", action="set", value=True,
                 rationale="Navigation, cookie banners and footers appear on every page and would dominate the token counts."),
        ]),
    ]),
    7: ("dedup", [
        ("01-duplicates.json", [
            dict(id="dedup.near_duplicate_threshold", action="set", value=0.97,
                 rationale="Above this two units say the same thing. The canonical one is kept and the other is linked to it, never deleted."),
            dict(id="dedup.exact_hash", action="set", value=True,
                 rationale="Identical sha256 is a free, exact check. Run it before any expensive similarity work."),
            dict(id="dedup.merge.keep", action="set", value="oldest",
                 rationale="The first version is canonical; later copies point at it. Deleting instead of linking would lose the provenance."),
            dict(id="dedup.cross_family", action="set", value=False,
                 rationale="A Python function and a JavaScript function that look similar are not duplicates. Only compare inside a family."),
        ]),
    ]),
    8: ("retrieval", [
        ("01-ranking.json", [
            dict(id="retrieval.top_k", action="set", value=8,
                 rationale="Eight chunks is about 10 KB of context: enough to answer, small enough to fit beside the model."),
            dict(id="retrieval.candidates", action="set", value=60,
                 rationale="Retrieve wide, rank narrow. 60 candidates is cheap and keeps recall from depending on one scorer."),
            dict(id="retrieval.fusion", action="set", value={"bm25": 1.0, "vector": 1.0, "address": 0.35},
                 rationale="Lexical and semantic search fail on different queries. Address proximity is a tiebreaker, not a primary signal."),
            dict(id="retrieval.refuse_below", action="set", value=0.02,
                 rationale="Under this the answer is 'not in the store'. A confident answer from weak evidence is the one failure mode that destroys trust."),
        ]),
    ]),
    9: ("training", [
        ("01-corpus.json", [
            dict(id="training.dedupe_before", action="set", value=True,
                 rationale="Duplicate shingles inflate loss improvement without teaching anything. Deduplicate before sharding."),
            dict(id="training.val_fraction", action="set", value=0.02,
                 rationale="A held-out slice, taken by hash of document id so it is stable across runs."),
            dict(id="training.tokens_per_param", action="set", value=20,
                 rationale="Chinchilla ratio. Below 20 tokens per parameter the model is undertrained, not small."),
            dict(id="training.seq_len", action="set", value=256,
                 rationale="Context length for the first rung. Raising it multiplies attention cost quadratically."),
            dict(id="training.checkpoint_keep", action="set", value=2,
                 rationale="Keep the last two. A checkpoint that will not load is worth nothing; the previous one is worth everything."),
        ]),
    ]),
    10: ("preference", [
        ("01-defaults.json", [
            dict(id="defaults.embed_backend", action="prefer", value="hash",
                 rationale="The hash backend needs nothing and is deterministic. Switch to ollama or sentence-transformers when you want better recall."),
            dict(id="defaults.search_backend", action="prefer", value="auto",
                 rationale="auto tries a keyed backend, then DuckDuckGo, then the fixture index. It never fails because of a missing key."),
            dict(id="output.format", action="prefer", value="text",
                 rationale="Plain text in the terminal; --json on any command when a script is reading it."),
            dict(id="output.no_emoji", action="prefer", value=True,
                 rationale="The output is read in a terminal, a log and a diff. Emoji break two of those three."),
            dict(id="output.max_answer_chars", action="prefer", value=1200,
                 rationale="Long answers get skimmed. The citations are there when more is needed."),
        ]),
    ]),
}


def main() -> None:
    for level, (name, files) in LEVELS.items():
        folder = os.path.join(ROOT, f"level-{level:02d}")
        os.makedirs(folder, exist_ok=True)
        for filename, rules in files:
            doc = {
                "level": level,
                "name": name,
                "description": f"Level {level}: {name}. Lower level numbers win.",
                "rules": rules,
            }
            path = os.path.join(folder, filename)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            print("wrote", path, len(rules), "rules")


if __name__ == "__main__":
    main()
