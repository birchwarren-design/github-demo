# roast-room: hardened `check_quotes.py`

A drop-in replacement for `.claude/skills/roast-room/scripts/check_quotes.py` in
[nateherkai/roast-room](https://github.com/nateherkai/roast-room). Same command line
(`python3 check_quotes.py <session folder>`), same exit code 1 on bad quotes.

## What changed and why

| Old behaviour | New behaviour |
|---|---|
| Quote checked against all brains joined together, so a misattributed quote passed | Checked against the **attributed seat's** brain, and the **cited S-id must sit next to it** there. Ids are per seat (every brain has an S001), so anything less is not a citation check. Reports `WRONG_SEAT` and `WRONG_ID` separately. |
| Only `(S###)` / `[S###]` within 40 chars was seen; `(s095)`, `(S95)`, `(Source: S095)`, wrapped lines, >400 chars all skipped | Any bracket group with an S-number after the quote is checked, case-insensitive, 1 to 4 digits, any prefix. `--strict` also fails non-canonical forms. |
| Quotes under 20 chars or 6 words skipped | Every attributed quote is checked |
| Missing folder / no quotes found: exit 0 | Exit 2. `--allow-empty` if zero quotes is expected. |
| A made-up quote repeated in a later round or the verdict counted as "internal" | Unverified quotes are blanked from the session history, so every repeat is flagged with file and line |
| `round-10` read before `round-2` | Numeric order |
| `...` elisions always failed | Each piece must be found in the same brain, near the cited id |
| Brain files joined with a space, so a "quote" could span two files | Files joined with a separator norm() never produces |
| Non-ASCII stripped (`café` became `caf`) | Unicode letters kept |

## Install

Copy `scripts/check_quotes.py` over the skill's copy. With no flags it reads
`../brains` and `../roster.json` next to the script, same as before. Roster names
(`Hormozi`, `Leila`, `Graham`) are used to work out which seat a quote belongs to:
the name inside the citation first, then the nearest `##` heading or `SEAT:` line.

## Tests

```
python3 -m unittest discover -s roast-room/tests -v
```

Against the three example sessions upstream, the script verifies 59 of 62
attributed quotes (2 more are seats quoting earlier cards) and flags one real
problem: `round-2.md:130` of `raise-coaching-price` elides a Buffett quote and
changes "they're" to "are".
