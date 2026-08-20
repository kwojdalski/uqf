# q/kdb+ language reference - edge cases relevant to a pure function library

Adapted from the [TorQ](https://github.com/DataIntellectTech/TorQ)
`.claude/skills/torq-developer/q-language-reference.md` (MIT licensed),
trimmed to what applies to a standalone q *function library* like uqf: no
processes, no IPC, no tickerplant/RDB, no tables on disk. If you're touching
`src/*.q` or `tests/*.q` in this repo and want more than the summary in
`SKILL.md`, this is the deeper reference. Everything TorQ-process-specific
(config layering, `.servers.*`, tickerplant/gateway/subscriptions, EOD
timers) has been dropped as not applicable here.

## 1. Type system

| Type# | Char | Name | Null | Example |
|---|---|---|---|---|
| -1h | b | boolean | `0b` | `1b` |
| -6h | i | int | `0Ni` | `42i` |
| -7h | j | long | `0N` | `42` |
| -9h | f | float | `0n` | `1.5` |
| -11h | s | symbol | `` ` `` | `` `sym `` |
| -14h | d | date | `0Nd` | `2024.01.01` |
| 0h | - | mixed list | - | `(1;2.0;"a")` |
| 10h | - | char vector (string) | - | `"hello"` |
| 98h | - | table | - | `([]a:1 2 3)` |
| 99h | - | dictionary | - | `` `a`b!1 2 `` |
| 100h+ | - | function/lambda | - | `{x+y}` |

**Atom vs vector**: `type 42` is `-7h` (negative = atom); `type 42 43` is
`7h` (positive = vector). This matters in this repo because most `src/*.q`
functions are written to work on either - see how `.uqf.inv_ncdf` branches
on `0>type p` to decide atom vs each-mapped vector handling.

**Mixed-list promotion**: `1 2 3` is a long vector (`7h`); `1 2 3.0` is
promoted to a float vector (`9h`) because one element is a float; but
`(1;2.0)` (parens+semicolon) stays a mixed list (`0h`) - int and float do
NOT auto-promote inside explicit parens the way a space-separated literal
does.

**Null comparisons**: `0N = 0N` is `0b` - null never equals null via `=`,
unlike SQL's `IS NULL`. Always use the `null` function: `null 0N` is `1b`.
Relevant if uqf ever needs to guard against missing/null rate or price
inputs.

**Casting**: `` `int$2147483648 `` silently overflows to `0Ni` (null) rather
than erroring - be careful casting large longs down to int/short.
`` `year$d ``, `` `mm$d ``, `` `dd$d `` (used in `daycount.q`) extract date
components as ints.

## 2. Iterators and adverbs

```
f each 1 2 3          / same as f'[1 2 3]
{x+y}'[1 2 3;10 20 30]   / each-both: element-wise over two matching lists
```

**Fold (over, `/`) with vs without a seed** - this is the exact mechanism
`.uqf.horner_eval` uses:
```
(+/) 1 2 3     / 6   - no seed: first element used as seed
0 +/ 1 2 3     / 6   - explicit seed 0
1 +/ 1 2 3     / 7   - explicit seed 1
```
To pass an explicit seed to a *derived* function (not a bare operator like
`+`), you must use **bracket** application: `f[x;]/[seed;list]`. Writing
`(f[x;]/) (seed;list)` instead passes a single 2-tuple as one argument and
silently does the wrong thing - this bit `horner_eval` during development
(see git history) and is worth remembering before writing any new fold.

**Scan (`\`) includes the seed in its output**: `0 +\ 1 2 3` is `0 1 3 6`;
`(+\) 1 2 3` (no seed) is `1 3 6`.

**Each-prior (`':`)**: `(-':) 1 3 6 10` gives `1 2 3 4` (successive
differences, first element diffed against itself). Useful if uqf ever adds
a rolling-return or day-over-day helper.

## 3. Error handling

```
@[f;x;handler]           / protected eval: run f[x], call handler[errmsg] on error
.[f;(x;y);handler]       / same, for a multi-arg f
'"my error string"       / signal/throw a string error
'`myerror                / signal/throw a symbol error
```

This is exactly the pattern qUnit's `assertError`/`assertThrows` rely on
(they call your function under `@[...]` internally), and it's how
`inv_ncdf`, `year_frac` and `cross_book` in this repo validate their inputs
(`'"inv_ncdf: p must be strictly between 0 and 1"`, etc.) - always signal
with a *string* error that says which function and what was wrong, not a
bare symbol, so a caller's error message is actually useful.

**Only the innermost trap catches an error** - if a function you call
already wraps its own risky code in `@[...]`, an outer `@[...]` around your
call won't see errors that the inner one already handled.

## 4. Namespaces

```
\d .uqf
myfunc:{x+1}   / becomes .uqf.myfunc
\d .
otherfunc:{x+1}  / becomes .otherfunc (root)
```

This is exactly the pattern every file in `src/` uses. Two traps worth
knowing:

- **`\d` persists for the rest of the file** until the next `\d` - forgetting
  the closing `\d .` at the bottom of a `src/*.q` file would leak later
  definitions into `.uqf` (or wherever you loaded next) unintentionally.
- **Local variables shadow globals, and don't leak out**: `f:{a:20;a}` sets
  a *local* `a` even if a global `a` already exists; the global is
  untouched unless you explicitly use `::` (e.g. `g:{a::20}`). This is
  exactly why qUnit's test functions can freely reuse names like `t`, `s`,
  `k` as locals without clobbering anything in `.uqf`.

**Max 8 parameters per function.** None of uqf's functions are close to
this limit (the largest, `gk_call`/`gk_put`/the Greeks, take 6), but if a
future function would need more than 8, pass a dictionary instead:
`{[args] args[`a]+args[`b]}[`a`b!1 2]`.

## 5. Date arithmetic (relevant to `daycount.q`)

```
2024.01.01 + 1            / 2024.01.02  (int+date=date)
2024.01.01 + 1.0          / type error  (float+date not allowed)
2024.01.02 - 2024.01.01   / 1           (date-date=int, NOT date - this is
                                          exactly what dcf_act_360/dcf_act_365
                                          divide by 360/365)
```

Always add/subtract dates with **ints**, never floats, and remember
date-minus-date gives a plain int day count, not another date.

## 6. Common idioms worth knowing

```
@[value;`var;default]      / read var, or default if it doesn't exist
42^0N                      / 42 - fills a null with a default (^ fills nulls)
"result: ",(string 42)     / "result: 42" - string concatenation
```

`^` (fill) is a handy alternative to `if[null x; x:default]` if uqf ever
needs to default a missing/null input, though the current style in this
repo (explicit `if[...; '"..."]` validation, e.g. in `inv_ncdf`) is
preferred for anything that should be a hard error rather than a silent
default - reserve `^` for genuinely optional parameters.
