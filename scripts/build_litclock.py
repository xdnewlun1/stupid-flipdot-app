"""Builds apps/daily_data/litclock.json (the literature clock) from public-domain books.

Download plain-text books from Project Gutenberg into books/ (pg<ID>.txt saved as books/<ID>.txt; only
books in the public domain worldwide: published before 1930, author and translator dead 70+ years), then:
    python3 scripts/build_litclock.py out.json
and wrap the result as {"source": ..., "minutes": ...}. It finds times of day written out in the text
("twenty minutes past seven", "half-past nine", "the 9.15", "at noon") and keeps up to three quotes a minute."""
import json, re, sys, unicodedata, glob, collections, os

NUM = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
def minute_words():
    out = dict((k, v) for k, v in NUM.items() if 1 <= v <= 19)
    for t, tv in TENS.items():
        out[t] = tv
        for k, v in NUM.items():
            if 1 <= v <= 9:
                out[f"{t}-{k}"] = tv + v
                out[f"{t} {k}"] = tv + v
    return out
MW = minute_words()
HOURS = {k: v for k, v in NUM.items() if 1 <= v <= 12}
H = r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|1[0-2]|[1-9])"
M = "(" + "|".join(sorted((re.escape(k) for k in MW), key=len, reverse=True)) + r"|[1-5]?[0-9])"
OCLOCK = r"o['’]?\s?clock"

def hnum(s):
    s = s.lower()
    return HOURS.get(s) if s in HOURS else int(s)
def mnum(s):
    s = s.lower().replace("‐", "-")
    return MW[s] if s in MW else int(s)

PATTERNS = [
    # "twenty minutes past seven", "five-and-twenty minutes to six" (simple form only)
    (re.compile(rf"\b{M}\s+minutes?\s+(past|after|to|before)\s+{H}(\s+{OCLOCK})?\b", re.I),
     lambda m: (hnum(m.group(3)), mnum(m.group(1)), m.group(2).lower() in ("to", "before"))),
    (re.compile(rf"\b(?:a\s+)?quarter\s+(past|after|to|before)\s+{H}(\s+{OCLOCK})?\b", re.I),
     lambda m: (hnum(m.group(2)), 15, m.group(1).lower() in ("to", "before"))),
    (re.compile(rf"\bhalf[\s-]+past\s+{H}(\s+{OCLOCK})?\b", re.I), lambda m: (hnum(m.group(1)), 30, False)),
    (re.compile(rf"\b(?:five|ten|twenty|twenty-five)\s+(past|to)\s+{H}\b", re.I), None),  # handled below
    (re.compile(rf"\b{H}\s+{OCLOCK}\b", re.I), lambda m: (hnum(m.group(1)), 0, False)),
    # 7.42 / 7:42 with a cue word before or a.m./p.m. after
    (re.compile(r"\b(?:at|by|till|until|the|about|is|was|it's|nearly|past)\s+(1[0-2]|0?[1-9]|1[3-9]|2[0-3])[.:]([0-5][0-9])\b(?![.,]?\d)(?!\s*(?:per|%|pounds|dollars|francs|shillings|guineas|miles|feet|inches|yards))", re.I),
     lambda m: (int(m.group(1)), int(m.group(2)), False)),
    # "at seven thirty", "at eight forty-five"
    (re.compile(rf"\b(?:at|by|till|until)\s+(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(ten|fifteen|twenty|thirty|forty|fifty|twenty-five|thirty-five|forty-five|fifty-five|oh[\s-](?:one|two|three|four|five|six|seven|eight|nine))\b", re.I),
     lambda m: (hnum(m.group(1)), 0 if False else mnum(m.group(2).lower().replace("oh-", "").replace("oh ", "")), False)),
    (re.compile(r"\b(?:at|by|till|until|it was|it is|struck)\s+(noon|midday|midnight)\b", re.I),
     lambda m: (12 if m.group(1).lower() != "midnight" else 0, 0, None)),
]
SHORT = re.compile(rf"\b(?:at|was|is|by|till|until|about|nearly|almost)\s+(five|ten|twenty|twenty-five)\s+(past|to)\s+{H}(?![-\w])(?!\s+(?:hundred|thousand|feet|foot|miles|years|yards|inches|pounds|shillings|pence|days|weeks|months|men|women|people|times|per|of|and|or|guineas|dollars|francs|leagues|minutes|hours))", re.I)
SHORT_W = {"five": 5, "ten": 10, "twenty": 20, "twenty-five": 25}

AM_CUES = re.compile(r"\b(a\.\s?m\.|in the morning|this morning|next morning|of the morning|dawn|daybreak|sunrise|breakfast)\b", re.I)
PM_CUES = re.compile(r"\b(p\.\s?m\.|in the afternoon|this afternoon|in the evening|this evening|of the evening|tonight|to-night|dinner|supper|sunset|dusk)\b", re.I)
NIGHT = re.compile(r"\b(at night|in the night|of the night|midnight)\b", re.I)

def clean(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", " - ").replace("–", "-").replace("--", " - ").replace("_", "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip()

def body(text):
    a = re.search(r"\*\*\* ?START OF.*?\*\*\*", text)
    b = re.search(r"\*\*\* ?END OF", text)
    return text[a.end() if a else 0: b.start() if b else len(text)]

SENT = re.compile(r'[^.!?]*(?:[.!?]+["\')]*|$)')
def sentences(par):
    out, pos = [], 0
    for m in re.finditer(r'[.!?]+["\')]*\s+(?=["\'(]?[A-Z])', par):
        out.append((pos, m.end()))
        pos = m.end()
    out.append((pos, len(par)))
    return out

def meta(text, path):
    t = re.search(r"^Title:\s*(.+)$", text, re.M); a = re.search(r"^Author:\s*(.+)$", text, re.M)
    title = clean(t.group(1)) if t else os.path.basename(path)
    title = re.split(r"[:;]", title)[0].strip()
    author = clean(a.group(1)) if a else ""
    author = re.sub(r"^graf ", "", author).replace("Baroness Emmuska Orczy Orczy", "Baroness Orczy")
    fix = {"The innocence of Father Brown": "The Innocence of Father Brown", "The wisdom of Father Brown": "The Wisdom of Father Brown",
           "His last bow": "His Last Bow", "The three musketeers": "The Three Musketeers", "My man Jeeves": "My Man Jeeves",
           "The war of the worlds": "The War of the Worlds", "The strange case of Dr. Jekyll and Mr. Hyde": "Dr Jekyll and Mr Hyde",
           "Frankenstein": "Frankenstein", "Three men in a boat (to say nothing of the dog)": "Three Men in a Boat",
           "Moby Dick": "Moby-Dick", "A Christmas Carol in Prose": "A Christmas Carol", "The Works of Edgar Allan Poe — Volume 2": "Tales",
           "Gulliver's Travels into Several Remote Nations of the World": "Gulliver's Travels", "The Adventures of Tom Sawyer, Complete": "Tom Sawyer",
           "Jane Eyre": "Jane Eyre", "The Invisible Man": "The Invisible Man", "The Secret Agent": "The Secret Agent"}
    title = fix.get(title, title)
    if title.startswith("The Works of Edgar"): title = "Tales"
    return title, author

found = collections.defaultdict(list)
for path in sorted(glob.glob("books/*.txt")):
    raw = open(path, encoding="utf-8", errors="replace").read()
    title, author = meta(raw, path)
    text = body(raw)
    for par in re.split(r"\n\s*\n", text):
        par = clean(par)
        if len(par) < 20:
            continue
        sents = sentences(par)
        hits = []
        for rx, fn in PATTERNS:
            if fn is None:
                continue
            for m in rx.finditer(par):
                try:
                    h, mi, to = fn(m)
                except (ValueError, KeyError):
                    continue
                cue = re.match(r"(?i)(at|by|till|until|the|about|is|was|it's|nearly|past|it was|it is|struck)\s", m.group())
                hits.append((m.start(1) if cue else m.start(), m.end(), h, mi, to))
        for m in SHORT.finditer(par):
            after = par[m.end():m.end() + 12].lower()
            if re.match(r"\W*(that|but|on|against|in|you)\b", after) or re.search(r"\b(odds|chances?|bet|betting|quoted|outsider|wager)\b", par, re.I):
                continue
            hits.append((m.start(1), m.end(), hnum(m.group(3)), SHORT_W[m.group(1).lower()], m.group(2).lower() == "to"))
        # keep the longest match at each spot
        hits.sort(key=lambda x: (x[0], -(x[1] - x[0])))
        last_end = -1
        for s, e, h, mi, to in hits:
            if s < last_end:
                continue
            last_end = e
            if mi is None or not (0 <= mi < 60) or h is None or h > 23:
                continue
            if to:
                if mi == 0: continue
                h, mi = (h - 1) % 12 or 12, 60 - mi
            # sentence(s) around it
            si = next(i for i, (a, b) in enumerate(sents) if a <= s < b or (i == len(sents) - 1))
            a, b = sents[si]
            quote = par[a:b].strip()
            off = a + (len(par[a:b]) - len(par[a:b].lstrip()))
            if len(quote) < 50 and si > 0:
                a = sents[si - 1][0]; quote = par[a:b].strip(); off = a
            if len(quote) < 50 and si + 1 < len(sents):
                b = sents[si + 1][1]; quote = par[a:b].strip()
            ps, pe = s - off, e - off
            if len(quote) > 220:
                # trim to a window around the phrase on word boundaries
                lo = max(0, ps - 90); hi = min(len(quote), pe + 110)
                lo = quote.rfind(" ", 0, lo) + 1 if lo > 0 else 0
                hi2 = quote.find(" ", hi); hi = len(quote) if hi2 < 0 or hi >= len(quote) else hi2
                pre = "..." if lo > 0 else ""; post = "..." if hi < len(quote) else ""
                quote = pre + quote[lo:hi].strip() + post
                ps, pe = ps - lo + len(pre), pe - lo + len(pre)
            if quote[ps:pe].lower() != par[s:e].lower():
                continue
            if len(quote) > 240 or len(quote) < 25:
                continue
            ctx = quote[max(0, ps - 45):pe + 45]
            right = quote[pe:pe + 8]
            explicit_am = re.match(r"\W{0,3}a\.?\s?m\b", right, re.I); explicit_pm = re.match(r"\W{0,3}p\.?\s?m\b", right, re.I)
            if h > 12:
                hours = [h]
            elif explicit_pm:
                hours = [h % 12 + 12]
            elif explicit_am:
                hours = [h % 12]
            elif AM_CUES.search(ctx) and not PM_CUES.search(ctx):
                hours = [h % 12]
            elif PM_CUES.search(ctx) and not AM_CUES.search(ctx):
                hours = [h % 12 + 12]
            elif NIGHT.search(ctx):
                hours = [h % 12 + 12] if 7 <= h <= 11 else [h % 12]
            else:
                hours = [h % 12, h % 12 + 12]
            if "noon" in par[s:e].lower() or "midday" in par[s:e].lower():
                hours = [12]
            for hh in hours:
                found[f"{hh:02d}:{mi:02d}"].append([quote, ps, pe, title, author])

# choose up to 3 per minute: prefer 60-180 chars, different books
out = {}
for k in sorted(found):
    cands = found[k]
    seen_q = set(); uniq = []
    for c in cands:
        if c[0] in seen_q: continue
        seen_q.add(c[0]); uniq.append(c)
    uniq.sort(key=lambda c: (abs(len(c[0]) - 120) // 40, c[3]))
    pick, books = [], set()
    for c in uniq:
        if c[3] in books: continue
        pick.append(c); books.add(c[3])
        if len(pick) == 3: break
    out[k] = pick
print(len(out), "minutes covered of 1440;", sum(len(v) for v in out.values()), "quotes", file=sys.stderr)
json.dump(out, open(sys.argv[1], "w"), separators=(",", ":"))
