# Formulae, as formulae

A search box that cannot see `x²` is a search box for prose. Formulae are the
part of a technical page that a tokeniser destroys:

| written | means | a text search sees |
|---|---|---|
| `a + b` | the same as `b + a` | two different strings |
| `\left( x \right)` | `(x)` | a command |
| `\frac{1}{2}` | `0.5` | a command and three digits |
| `∫₀¹ x² dx` | `\int_0^1 x^2 dx` | emoji-adjacent characters |

`vrusha math` canonicalises first, then matches structurally.

```bash
vrusha extract --dir ./papers --licence MIT     # formulae are read from the pages
vrusha math "e^{i\pi}+1=0"                      # exact
vrusha math "b^2-4ac"                           # a subformula of the quadratic
vrusha math                                     # what is stored
```

## What canonicalisation does

| step | why |
|---|---|
| unicode to LaTeX (`∫₀¹` → `\int_0^1`, `x²` → `x^2`) | a formula copied off a rendered page is unicode |
| drop rendering commands (`\left`, `\right`, `\displaystyle`, `\,`) | they change how it looks, not what it is |
| `\frac{a}{b}` → `(a)/(b)`, `\sqrt{a}` → `sqrt(a)` | brace-aware, so a nested `\frac{-b \pm \sqrt{b^2-4ac}}{2a}` parses |
| order the operands of `+` and `*` | `b+a` is `a+b` |
| order each side of `=` separately | `=` binds looser than `+`; sorting the whole thing invents a formula |
| strip parentheses around the whole expression | `\left(a+b\right)` is `a+b` |

## How a match is scored

| kind | score | meaning |
|---|---|---|
| `exact` | 1.00 | the same canonical form |
| `subformula` | 0.90 | the query appears inside the stored formula |
| `subformula` (in a bracket group) | 0.85 | the query appears in a part of it |
| `symbols` | 0.25–0.50 | most of the query's symbols appear: a candidate, not an answer |

Every hit carries its document, URL and licence, because a formula without a
source is a rumour.

## What it does not do

- **No LaTeX → MathML.** That is a parser and a half; the published systems
  (MathWebSearch, MIaS, Approach Zero) each spend a project on it.
- **No semantic equivalence** beyond commutative reordering. `\sin^2+\cos^2=1`
  and `1=\sin^2+\cos^2` are the same fact and different formulae here.
- **No substructure matching at scale.** For that, [PyA0](https://github.com/approach0/pya0)
  wraps Approach Zero's C engine and ships prebuilt indexes; it belongs behind
  the router as an optional backend, the way RagForge does for vectors.
- **No formula extraction from PDF text.** The PDF reader gets positioned text,
  and a formula in a PDF is usually split across runs and glyphs. The page's
  HTML or MathML is the source that works.
