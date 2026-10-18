"""Read-only provider catalogue identities and preferred editions for browse views."""
import re
import unicodedata


def mappings(item):
    result = []
    for row in item.get("provider_mappings") or []:
        if not isinstance(row, dict) or row.get("available") is False:
            continue
        provider = row.get("provider_instance") or row.get("provider_domain") or ""
        item_id = str(row.get("item_id") or "")
        pair = (provider, item_id)
        if provider and provider != "library" and item_id and pair not in result:
            result.append(pair)
    return result


def normalized(value):
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def edition(value, album=False):
    labels = "explicit|clean|remaster(?:ed)?"
    if album:
        labels += "|deluxe|expanded|anniversary|bonus tracks|special edition"
    value = re.sub(r"[\(\[][^(\)]*(?:" + labels + r")[^\)\]]*[\)\]]", " ", str(value or ""), flags=re.I)
    value = re.sub(r"\s[-–—:]\s[^-]*(?:" + labels + r").*$", " ", value, flags=re.I)
    return re.sub(r"^(?:" + labels + r")(?: edition| version| mix)?$", "", value, flags=re.I)


def semantic(item):
    kind = item.get("media_type")
    if kind not in ("album", "track"):
        return None
    artists = item.get("artists")
    names = [a.get("name") for a in artists if isinstance(a, dict) and a.get("name")] if isinstance(artists, list) else [item.get("artist")]
    names = sorted(normalized(n) for n in names if n)
    if not names:
        return None
    title = edition(item.get("name"), kind == "album")
    if kind == "album":
        title = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", title).strip() or str(item.get("name") or "")
    title = normalized(title)
    if not title:
        return None
    version = "" if kind == "album" else normalized(edition(item.get("version")))
    return kind, tuple(names), title, version


def quality(item):
    formats = [item.get("audio_format"), (item.get("metadata") or {}).get("audio_format")]
    formats += [m.get("audio_format") for m in item.get("provider_mappings") or [] if isinstance(m, dict) and m.get("available") is not False]
    def score(fmt):
        def number(key):
            try: return int(fmt.get(key) or 0)
            except (TypeError, ValueError): return 0
        return (int(str(fmt.get("content_type") or "").lower() in {"flac", "alac", "wav", "pcm", "aiff", "dsd", "dsf"}), number("bit_depth"), number("sample_rate"), number("bit_rate") or number("bitrate"))
    return max((score(fmt) for fmt in formats if isinstance(fmt, dict)), default=(0, 0, 0, 0))


def rank(item):
    providers = item.get("provider_mappings") or []
    available = item.get("available") is not False and (not providers or any(m.get("available") is not False for m in providers if isinstance(m, dict)))
    explicit = item.get("explicit", (item.get("metadata") or {}).get("explicit"))
    return int(available), (1 if explicit is None else 2 if explicit else 0), quality(item), int(item.get("provider") == "library")


def merge(rows):
    rows = [r for r in rows if isinstance(r, dict)]
    parents = list(range(len(rows)))
    def root(i):
        while parents[i] != i:
            i = parents[i]
        return i
    seen = {}
    for i, row in enumerate(rows):
        kind = row.get("media_type", "")
        identities = []
        if row.get("provider") and row.get("item_id") is not None:
            identities.append(("id", kind, row["provider"], str(row["item_id"])))
        if row.get("uri"):
            identities.append(("uri", kind, row["uri"]))
        identities += [("id", kind, provider, item_id) for provider, item_id in mappings(row)]
        key = semantic(row)
        if key:
            identities.append(("release", key))
        for identity in identities:
            if identity in seen:
                parents[root(i)] = root(seen[identity])
        for identity in identities:
            seen[identity] = i
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(root(i), []).append(row)
    # Kotlin maxWithOrNull keeps the first row on ties.
    return [max(group, key=rank) for group in groups.values()]
