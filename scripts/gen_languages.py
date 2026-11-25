"""Generate vrusha/languages/profiles/*.json.

Run once: python scripts/gen_languages.py

A profile is facts, not code. If a fact here is wrong, the harvester and the
chunker are wrong, so each entry carries a note saying what it is for.
"""
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "vrusha", "languages", "profiles")

NATURAL = [
    {
        "code": "en", "name": "English", "kind": "natural", "family_id": 1,
        "script": "Latn", "direction": "ltr",
        "sentence_end": [".", "!", "?"],
        "stopwords": ["the", "of", "and", "to", "in", "a", "is", "that", "for", "it",
                      "as", "with", "was", "on", "be", "by", "this", "are", "or", "from"],
        "markers": ["the", "and", "of", "to", "is", "that", "with", "for", "was", "are"],
        "grammar": {
            "word_order": "SVO",
            "morphology": "analytic — word order and auxiliaries carry the grammar",
            "articles": "definite and indefinite articles are obligatory",
            "notes": "Hyphenated compounds and phrasal verbs mean a word boundary is not a meaning boundary.",
        },
    },
    {
        "code": "hi", "name": "Hindi", "kind": "natural", "family_id": 2,
        "script": "Deva", "direction": "ltr",
        "sentence_end": ["।", ".", "!", "?"],
        "stopwords": ["का", "के", "की", "है", "में", "से", "और", "को", "पर", "यह",
                      "था", "हो", "ने", "लिए", "जो", "कि", "भी", "नहीं", "तो", "हैं"],
        "markers": ["है", "के", "में", "का", "और", "को", "से", "की", "पर", "हैं"],
        "grammar": {
            "word_order": "SOV",
            "morphology": "agglutinative with postpositions — the case marker follows the noun",
            "script_note": "Devanagari is shared with Marathi; the marker words decide between them",
            "notes": "The danda (।) ends a sentence, not a full stop. Splitting on '.' alone merges sentences.",
        },
    },
    {
        "code": "mr", "name": "Marathi", "kind": "natural", "family_id": 3,
        "script": "Deva", "direction": "ltr",
        "sentence_end": ["।", ".", "!", "?"],
        "stopwords": ["आहे", "आणि", "या", "ते", "मध्ये", "ला", "च्या", "करून", "नाही", "होते"],
        "markers": ["आहे", "आणि", "मध्ये", "च्या", "ला", "ते", "करून", "नाही"],
        "grammar": {
            "word_order": "SOV",
            "morphology": "agglutinative; verb agreement carries person and gender",
            "notes": "Shares Devanagari with Hindi. Overlap in function words is why detection reports a confidence, not a verdict.",
        },
    },
    {
        "code": "ta", "name": "Tamil", "kind": "natural", "family_id": 4,
        "script": "Taml", "direction": "ltr",
        "sentence_end": [".", "!", "?"],
        "stopwords": ["மற்றும்", "ஒரு", "இந்த", "ஆகும்", "என்று", "இல்லை", "உள்ள"],
        "markers": ["மற்றும்", "ஒரு", "இந்த", "ஆகும்", "என்று"],
        "grammar": {
            "word_order": "SOV",
            "morphology": "agglutinative — suffixes stack case, number and person onto one stem",
            "notes": "A single Tamil word can be a whole English clause, so token counts are not comparable across languages.",
        },
    },
    {
        "code": "bn", "name": "Bengali", "kind": "natural", "family_id": 5,
        "script": "Beng", "direction": "ltr",
        "sentence_end": ["।", ".", "!", "?"],
        "stopwords": ["এবং", "এই", "হয়", "করে", "থেকে", "জন্য", "নেই", "আছে"],
        "markers": ["এবং", "এই", "হয়", "করে", "থেকে", "জন্য"],
        "grammar": {
            "word_order": "SOV",
            "morphology": "agglutinative with postpositions",
            "notes": "Danda ends a sentence. Conjunct consonants make byte length a poor proxy for word count.",
        },
    },
]

CODE = [
    {
        "code": "python", "name": "Python", "kind": "code", "family_id": 1,
        "extensions": [".py", ".pyi"], "comment": ["#"],
        "strings": ['"""', "'''", '"', "'"], "indent": "spaces",
        "chunk_strategy": "ast",
        "notes": "ast gives real function and class boundaries; no heuristic needed.",
    },
    {
        "code": "javascript", "name": "JavaScript", "kind": "code", "family_id": 2,
        "extensions": [".js", ".jsx", ".mjs", ".cjs"], "comment": ["//", "/*"],
        "strings": ['"', "'", "`"], "indent": "either",
        "chunk_strategy": "delimiter",
        "notes": "Arrow functions and callbacks mean a brace-depth split can cut a unit in half; the delimiter chunker checks balance before splitting.",
    },
    {
        "code": "typescript", "name": "TypeScript", "kind": "code", "family_id": 3,
        "extensions": [".ts", ".tsx"], "comment": ["//", "/*"],
        "strings": ['"', "'", "`"], "indent": "either",
        "chunk_strategy": "delimiter",
        "notes": "Generics add angle brackets, so the delimiter chunker must not treat < as a group opener.",
    },
    {
        "code": "java", "name": "Java", "kind": "code", "family_id": 4,
        "extensions": [".java"], "comment": ["//", "/*"],
        "strings": ['"'], "indent": "either", "chunk_strategy": "delimiter",
        "notes": "One public class per file; methods are the retrieval unit.",
    },
    {
        "code": "c", "name": "C", "kind": "code", "family_id": 5,
        "extensions": [".c", ".h"], "comment": ["//", "/*"], "strings": ['"'],
        "indent": "either", "chunk_strategy": "delimiter",
        "notes": "Preprocessor blocks are not statements. Keep #include with the chunk that uses it.",
    },
    {
        "code": "cpp", "name": "C++", "kind": "code", "family_id": 6,
        "extensions": [".cpp", ".cc", ".hpp", ".hxx"], "comment": ["//", "/*"],
        "strings": ['"'], "indent": "either", "chunk_strategy": "delimiter",
        "notes": "Templates and lambdas make brace counting approximate. Accept the approximation and record it.",
    },
    {
        "code": "go", "name": "Go", "kind": "code", "family_id": 7,
        "extensions": [".go"], "comment": ["//", "/*"], "strings": ['"', "`"],
        "indent": "tabs", "chunk_strategy": "delimiter",
        "notes": "Functions are top-level and brace-delimited; a depth-0 func is a clean unit.",
    },
    {
        "code": "rust", "name": "Rust", "kind": "code", "family_id": 8,
        "extensions": [".rs"], "comment": ["//", "/*"], "strings": ['"'],
        "indent": "spaces", "chunk_strategy": "delimiter",
        "notes": "impl blocks are the natural unit above fn; keep them whole where they fit.",
    },
    {
        "code": "sql", "name": "SQL", "kind": "code", "family_id": 9,
        "extensions": [".sql"], "comment": ["--", "/*"], "strings": ["'"],
        "indent": "either", "chunk_strategy": "line",
        "notes": "Statements end at a semicolon outside a string. One statement is one unit.",
    },
    {
        "code": "shell", "name": "Shell", "kind": "code", "family_id": 10,
        "extensions": [".sh", ".bash", ".zsh"], "comment": ["#"], "strings": ['"', "'"],
        "indent": "either", "chunk_strategy": "line",
        "notes": "A function definition is a unit; the rest of a script is a sequence of commands.",
    },
    {
        "code": "html", "name": "HTML", "kind": "code", "family_id": 11,
        "extensions": [".html", ".htm"], "comment": ["<!--"], "strings": ['"', "'"],
        "indent": "either", "chunk_strategy": "paragraph",
        "notes": "Harvested pages are not training code. HTML is stored as prose once tags are stripped.",
    },
    {
        "code": "css", "name": "CSS", "kind": "code", "family_id": 12,
        "extensions": [".css", ".scss"], "comment": ["/*"], "strings": ['"', "'"],
        "indent": "either", "chunk_strategy": "delimiter",
        "notes": "A rule block is the unit. At-rules nest, so balance matters.",
    },
    {
        "code": "json", "name": "JSON", "kind": "code", "family_id": 13,
        "extensions": [".json", ".jsonl"], "comment": [], "strings": ['"'],
        "indent": "either", "chunk_strategy": "line",
        "notes": "Structured data, not code. Chunked by top-level key where possible.",
    },
    {
        "code": "yaml", "name": "YAML", "kind": "code", "family_id": 14,
        "extensions": [".yaml", ".yml"], "comment": ["#"], "strings": ['"', "'"],
        "indent": "spaces", "chunk_strategy": "line",
        "notes": "Indentation is structure. A chunk that starts mid-block is unparseable, so split on top-level keys.",
    },
    {
        "code": "markdown", "name": "Markdown", "kind": "code", "family_id": 15,
        "extensions": [".md", ".markdown"], "comment": ["<!--"], "strings": ["`"],
        "indent": "either", "chunk_strategy": "paragraph",
        "notes": "Headings define sections; the paragraph chunker already splits on them.",
    },
]


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    for group in (NATURAL, CODE):
        for profile in group:
            path = os.path.join(ROOT, f"{profile['code']}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(profile, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            print("wrote", path)


if __name__ == "__main__":
    main()
