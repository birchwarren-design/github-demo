#!/usr/bin/env python3
"""
Check every quote in a council session that carries a source id against the
brain of the seat it is attributed to.

Usage:
  python3 check_quotes.py <session folder> [--brains DIR] [--roster FILE]
                          [--allow-empty] [--strict]

Drop-in replacement for .claude/skills/roast-room/scripts/check_quotes.py. With
no --brains/--roster it uses ../brains and ../roster.json next to this script.

Source ids are per seat: every brain has its own S001. So a quote marked
(S095) is checked against the brain of the seat that said it, and the cited id
must sit next to the quote in that brain (brains store quotes as "..." (S095)).

Each attributed quote lands in one bucket:
  VERIFIED     in the seat's brain, with a cited id next to it
  INTERNAL     not in a brain, but quotes text from an earlier round or the
               brief (a seat quoting another seat's card). Unverified quotes
               are blanked out of that earlier text first, so a made-up quote
               cannot launder itself by being repeated.
  WRONG_ID     in the seat's brain, but next to a different id
  WRONG_SEAT   not in the seat's brain, but in another seat's brain
  UNVERIFIED   in no brain and no earlier session text

Quotes shortened with "..." are split and every piece must be found, in the
same brain, near the cited id.

Exit codes:
  0  every attributed quote is VERIFIED or INTERNAL
  1  at least one quote is WRONG_ID, WRONG_SEAT or UNVERIFIED
  2  setup or usage error: missing session folder, no round files, no brains,
     or no attributed quotes at all (pass --allow-empty if that is expected)

--strict also fails on citations in a non-canonical form, such as (s95).
"""
import argparse
import json
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]

# A quoted span: curly pair, or straight pair. A span may wrap one line but
# not cross a blank line. finditer scans left to right, so straight quotes
# pair up in order instead of the closing quote of one span opening the next.
QUOTE_RE = re.compile(r"“([^“”]+?)”|\"([^\"]+?)\"", re.S)
# A citation group right after the quote: a bracket opening within 60 chars,
# with no other quote in between, containing at least one S-number.
TRAILER_RE = re.compile(r"[^\"“”\n]{0,60}?[\(\[]([^\(\)\[\]\n\"“”]{0,120})[\)\]]")
ID_RE = re.compile(r"\b[Ss]\s?(\d{1,4})\b")
CANONICAL_ID_RE = re.compile(r"\bS\d{3}\b")
ELLIPSIS_RE = re.compile(r"\s*(?:\[\s*(?:\.\s*){3}\]|\.\s?\.\s?\.|…)\s*")
SEP = " \x00 "  # file separator in normalized text; norm() never produces \x00
ID_WINDOW = 160  # normalized chars after a brain quote in which its id must appear


def norm(s):
    s = s.lower()
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"')):
        s = s.replace(a, b)
    s = re.sub(r"[^\w' ]+", " ", s)  # \w keeps accented letters
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def canon_id(n):
    return "s" + n.zfill(3)


def round_key(p):
    m = re.search(r"(\d+)", p.stem)
    return int(m.group(1)) if m else 0


def load_brains(brains_dir):
    """{slug: normalized text of every .md in that brain}, files kept apart."""
    brains = {}
    for d in sorted(p for p in brains_dir.iterdir() if p.is_dir()):
        parts = [norm(f.read_text(encoding="utf-8", errors="replace")) for f in sorted(d.rglob("*.md"))]
        if parts:
            brains[d.name] = SEP.join(parts)
    loose = [norm(f.read_text(encoding="utf-8", errors="replace")) for f in sorted(brains_dir.glob("*.md"))]
    if loose:
        brains["_shared"] = SEP.join(loose)
    return brains


def load_aliases(roster_path, brains):
    """Map lowercase seat names to a brain slug."""
    aliases = {}
    if roster_path and roster_path.exists():
        for seat in json.loads(roster_path.read_text(encoding="utf-8")).get("seats", []):
            slug = seat.get("slug")
            if slug not in brains:
                continue
            for k in ("name", "short", "key", "slug"):
                if seat.get(k):
                    aliases[seat[k].lower()] = slug
    for slug in brains:  # slug-derived fallbacks when there is no roster
        aliases.setdefault(slug.replace("-", " "), slug)
    # Last names as a fallback, unless two seats share one (two Hormozis) or
    # the roster already gave that word to a seat (Alex's short is "Hormozi").
    lasts = {}
    for slug in brains:
        lasts.setdefault(slug.split("-")[-1], []).append(slug)
    for last, slugs in lasts.items():
        if len(slugs) == 1:
            aliases.setdefault(last, slugs[0])
    return aliases


def seats_named(text, aliases):
    found = []
    low = text.lower()
    for alias in sorted(aliases, key=len, reverse=True):
        if re.search(r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])", low):
            slug = aliases[alias]
            if slug not in found:
                found.append(slug)
            low = re.sub(re.escape(alias), " ", low)  # "leila hormozi" must not also hit "hormozi"
    return found


def find_with_id(brain, pieces, ids):
    """Return (found_all_pieces, id_ok, ids_seen_near_quote)."""
    hay = " " + brain + " "
    near = set()
    id_ok = True
    for piece in pieces:
        needle = " " + piece + " "
        starts = [m.start() for m in re.finditer(re.escape(needle), hay)]
        if not starts:
            return False, False, set()
        piece_ok = False
        for st in starts:
            window = hay[st + len(needle): st + len(needle) + ID_WINDOW]
            window = window.split("\x00")[0]  # do not read into the next file
            seen = set(re.findall(r"\bs\d{3}\b", window))
            near |= seen
            if seen & ids:
                piece_ok = True
        id_ok = id_ok and piece_ok
    return True, id_ok, near


def heading_seat(text_before, aliases):
    for line in reversed(text_before.splitlines()):
        if line.startswith("#"):
            named = seats_named(line, aliases)
            return named[0] if len(named) == 1 else None
        m = re.match(r"\s*SEAT:\s*(.+)", line)
        if m:
            named = seats_named(m.group(1), aliases)
            if len(named) == 1:
                return named[0]
    return None


def extract(text, aliases):
    """Yield dicts for every quote; attributed ones carry ids and seat."""
    for m in QUOTE_RE.finditer(text):
        raw = m.group(1) if m.group(1) is not None else m.group(2)
        if "\n\n" in raw or len(raw) > 3000:
            continue  # not a real pair
        line_no = text.count("\n", 0, m.start()) + 1
        t = TRAILER_RE.match(text, m.end())
        ids, noncanon = [], []
        cite = ""
        if t:
            cite = t.group(1)
            for n in ID_RE.findall(cite):
                ids.append(canon_id(n))
            for tok in re.findall(r"\b[Ss]\s?\d{1,4}\b", cite):
                if not CANONICAL_ID_RE.fullmatch(tok):
                    noncanon.append(tok)
        if not ids:
            yield {"quote": raw, "line": line_no, "attributed": False, "span": m.span()}
            continue
        seat = None
        named = seats_named(text[m.end(): t.end()], aliases)
        if len(named) == 1:
            seat = named[0]
        if seat is None:
            seat = heading_seat(text[: m.start()], aliases)
        if seat is None:
            line_start = text.rfind("\n", 0, m.start()) + 1
            named = seats_named(text[line_start: m.start()], aliases)
            if len(named) == 1:
                seat = named[0]
        yield {
            "quote": raw, "line": line_no, "attributed": True, "span": m.span(),
            "ids": set(ids), "cite": cite, "noncanon": noncanon, "seat": seat,
        }


def classify(q, brains, earlier):
    pieces = [norm(p) for p in ELLIPSIS_RE.split(q["quote"])]
    pieces = [p for p in pieces if p]
    if not pieces:
        return "UNVERIFIED", "empty quote"
    seat = q["seat"]
    ids = q["ids"]
    order = [seat] if seat in brains else []
    others = [s for s in brains if s not in order]

    if order:
        found, id_ok, near = find_with_id(brains[seat], pieces, ids)
        if found and id_ok:
            return "VERIFIED", seat
        if found:
            got = ", ".join(sorted(s.upper() for s in near)) or "no id"
            return "WRONG_ID", f"in {seat} next to {got}, cited {', '.join(sorted(i.upper() for i in ids))}"
    else:
        # Seat unknown: accept a brain where the quote sits next to a cited id.
        for s in others:
            found, id_ok, _ = find_with_id(brains[s], pieces, ids)
            if found and id_ok:
                return "VERIFIED", f"{s} (seat inferred)"

    if all((" " + p + " ") in (" " + earlier + " ") for p in pieces):
        return "INTERNAL", "earlier round or brief"

    homes = [s for s in others if find_with_id(brains[s], pieces, ids)[0]]
    if order and homes:
        return "WRONG_SEAT", f"attributed to {seat}, found in {', '.join(homes)}"
    if not order and homes:
        return "WRONG_ID", f"found in {', '.join(homes)} but not next to the cited id"
    return "UNVERIFIED", f"not in {'brain ' + seat if order else 'any brain'}"


def blank_spans(text, spans):
    out = list(text)
    for a, b in spans:
        for i in range(a, b):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("session", type=Path)
    ap.add_argument("--brains", type=Path, default=SKILL_DIR / "brains")
    ap.add_argument("--roster", type=Path, default=SKILL_DIR / "roster.json")
    ap.add_argument("--allow-empty", action="store_true", help="exit 0 when no attributed quotes are found")
    ap.add_argument("--strict", action="store_true", help="fail on non-canonical citations like (s95)")
    args = ap.parse_args(argv)

    if not args.session.is_dir():
        print(f"ERROR: session folder not found: {args.session}", file=sys.stderr)
        return 2
    if not args.brains.is_dir():
        print(f"ERROR: brains folder not found: {args.brains}", file=sys.stderr)
        return 2
    brains = load_brains(args.brains)
    if not brains:
        print(f"ERROR: no brain .md files under {args.brains}", file=sys.stderr)
        return 2
    aliases = load_aliases(args.roster, brains)

    rounds = sorted(args.session.glob("round-*.md"), key=round_key)
    if not rounds:
        print(f"ERROR: no round-*.md files in {args.session}", file=sys.stderr)
        return 2
    files = rounds + [p for p in [args.session / "verdict.md"] if p.exists()]
    brief = args.session / "brief.md"
    earlier = norm(brief.read_text(encoding="utf-8", errors="replace")) if brief.exists() else ""

    counts = {k: 0 for k in ("VERIFIED", "INTERNAL", "WRONG_ID", "WRONG_SEAT", "UNVERIFIED")}
    problems, style = [], []
    unattributed = 0
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        bad_spans = []
        for q in extract(text, aliases):
            if not q["attributed"]:
                unattributed += 1
                continue
            bucket, detail = classify(q, brains, earlier)
            counts[bucket] += 1
            if bucket in ("WRONG_ID", "WRONG_SEAT", "UNVERIFIED"):
                problems.append((bucket, f.name, q["line"], q["quote"], detail))
                bad_spans.append(q["span"])
            if q["noncanon"]:
                style.append((f.name, q["line"], ", ".join(q["noncanon"])))
        earlier += SEP + norm(blank_spans(text, bad_spans))

    total = sum(counts.values())
    print(
        f"attributed quotes: {total} | verified: {counts['VERIFIED']} | internal: {counts['INTERNAL']} | "
        f"wrong id: {counts['WRONG_ID']} | wrong seat: {counts['WRONG_SEAT']} | unverified: {counts['UNVERIFIED']} | "
        f"unattributed quotes (not checked): {unattributed}"
    )
    for bucket, name, line, quote, detail in problems:
        print(f"  {bucket} [{name}:{line}] \"{quote[:160]}\" ({detail})")
    for name, line, toks in style:
        print(f"  {'STYLE' if args.strict else 'note'} [{name}:{line}] non-canonical source id: {toks} (use S###)")

    if total == 0 and not args.allow_empty:
        print("ERROR: no attributed quotes found. If that is expected, pass --allow-empty.", file=sys.stderr)
        return 2
    if problems or (args.strict and style):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
