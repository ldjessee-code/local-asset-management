"""Temporary evidence scan. Not part of the product."""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

RAW = Path("F:/Dropbox/TheCourt/research/_jobs/patreon-full-files/evidence/raw")
SINGLE = Path("F:/Dropbox/TheCourt/research/_jobs/patreon-full-files/evidence/single")
IMG = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
FULL_EXT = {
    ".zip", ".rar", ".7z", ".pdf", ".psd", ".uvtt", ".mp3", ".wav",
    ".mp4", ".mov", ".blend", ".txt", ".json", ".dd2v", ".dungeondraft",
}


def host_of(url: str) -> str:
    rest = url.split("://", 1)[-1]
    host = rest.split("/", 1)[0].split("?", 1)[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def tail_ext(url: str) -> tuple[str, str]:
    path = url.split("?", 1)[0].split("#", 1)[0]
    tail = path.rstrip("/").split("/")[-1]
    ext = ""
    if "." in tail and not tail.startswith("."):
        ext = "." + tail.rsplit(".", 1)[-1].lower()[:16]
    return tail[:90], ext


def collect_links(obj, acc, depth=0):
    if depth > 14:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in ("href", "src", "url", "download_url", "thumb_url", "large_url") and isinstance(value, str) and value.startswith("http"):
                acc.append((key, value))
            elif isinstance(value, str) and value[:1] == "{" and "http" in value and len(value) < 2_000_000:
                try:
                    collect_links(json.loads(value), acc, depth + 1)
                except Exception:
                    for found in re.findall(r"https?://[^\s\"'<>]+", value):
                        acc.append(("str", found))
            else:
                collect_links(value, acc, depth + 1)
    elif isinstance(obj, list):
        for value in obj:
            collect_links(value, acc, depth + 1)


def rel_counts(resource):
    counts = {}
    for name, rel in (resource.get("relationships") or {}).items():
        data = (rel or {}).get("data")
        if isinstance(data, list):
            counts[name] = len(data)
        elif isinstance(data, dict):
            counts[name] = 1
        else:
            counts[name] = 0
    return counts


def index_included(payload):
    included = {}
    for item in payload.get("included") or []:
        if isinstance(item, dict) and item.get("id") is not None:
            included[(str(item.get("type") or ""), str(item["id"]))] = item
    return included


def main():
    content_states = Counter()
    post_file_shapes = Counter()
    embed_shapes = Counter()
    reward_rel = Counter()
    other_view_rel = Counter()
    json_nonimg = Counter()
    json_noext = Counter()
    html_hosts = Counter()
    year_reward = defaultdict(Counter)
    year_other_nonimg = defaultdict(Counter)
    media_name_ext = Counter()
    bare = Counter()
    post_file_examples = []
    nonimg_examples = []
    reward_included_nonimg = []
    image_attr_examples = []
    n_posts = 0
    empty_reward = 0
    list_by_id = {}

    for page in sorted(RAW.glob("*.json")):
        payload = json.loads(page.read_text(encoding="utf-8"))
        included = index_included(payload)
        for resource in payload.get("data") or []:
            if not isinstance(resource, dict):
                continue
            n_posts += 1
            attrs = resource.get("attributes") or {}
            pid = str(resource.get("id"))
            title = (attrs.get("title") or "").strip()
            pub = attrs.get("published_at") or ""
            year = pub[:4]
            can = bool(attrs.get("current_user_can_view"))
            is_reward = "reward" in title.lower()
            counts = rel_counts(resource)
            sig = "|".join("%s=%s" % item for item in sorted(counts.items()))
            list_by_id[pid] = {
                "year": year,
                "title": title,
                "can": can,
                "reward": is_reward,
                "sig": sig,
                "content": attrs.get("content"),
                "post_file": attrs.get("post_file"),
                "page": page.name,
            }
            if is_reward and can:
                reward_rel[sig] += 1
                if all(counts.get(k, 0) == 0 for k in ("attachments", "attachments_media", "audio", "images", "media")):
                    empty_reward += 1
            elif can:
                other_view_rel[sig] += 1
            content = attrs.get("content")
            if content is None:
                content_states["null"] += 1
            elif content == "":
                content_states["empty"] += 1
            else:
                content_states["html"] += 1
                links = []
                collect_links(content, links)
                for _kind, url in links:
                    html_hosts[host_of(url)] += 1
            post_file = attrs.get("post_file")
            if post_file is None:
                post_file_shapes["null"] += 1
            elif isinstance(post_file, dict):
                name = post_file.get("name")
                mime = str(post_file.get("mimetype") or post_file.get("mime_type") or "")
                url = post_file.get("url") or ""
                _tail, ext = tail_ext(url) if isinstance(url, str) else ("", "")
                shape = (
                    "keys=" + ",".join(sorted(post_file.keys())),
                    "name=" + ("set" if name else "empty"),
                    "ext=" + (ext or "none"),
                    "mime=" + (mime[:40] or "none"),
                )
                post_file_shapes[shape] += 1
                if len(post_file_examples) < 15 and (not name or ext not in IMG):
                    post_file_examples.append((pid, year, title[:42], name, mime, host_of(url) if url else "", ext, _tail))
            else:
                post_file_shapes[type(post_file).__name__] += 1
            embed = attrs.get("embed")
            if embed is None:
                embed_shapes["null"] += 1
            elif isinstance(embed, dict):
                embed_shapes[tuple(sorted(embed.keys()))] += 1
            else:
                embed_shapes[type(embed).__name__] += 1
            blob = attrs.get("content_json_string")
            jlinks = []
            if isinstance(blob, str) and blob:
                try:
                    collect_links(json.loads(blob), jlinks)
                except Exception:
                    content_states["bad-json"] += 1
            for kind, url in jlinks:
                host = host_of(url)
                tail, ext = tail_ext(url)
                if ext in IMG:
                    bucket = "img"
                elif ext:
                    bucket = "ext"
                    json_nonimg[host + " " + ext] += 1
                else:
                    bucket = "noext"
                    json_noext[host] += 1
                key = host + "|" + bucket + "|" + ext
                if is_reward:
                    year_reward[year][key] += 1
                    if bucket != "img" and len(nonimg_examples) < 30:
                        nonimg_examples.append((pid, year, title[:48], kind, host, ext or "noext", tail))
                elif bucket != "img":
                    year_other_nonimg[year][key] += 1
            for rel_name, rel in (resource.get("relationships") or {}).items():
                data = (rel or {}).get("data")
                items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
                for ref in items:
                    if not isinstance(ref, dict):
                        continue
                    item = included.get((str(ref.get("type") or ""), str(ref.get("id"))))
                    if not item:
                        bare["missing " + rel_name] += 1
                        continue
                    a = item.get("attributes") or {}
                    fname = a.get("file_name") or a.get("name") or ""
                    mime = str(a.get("mimetype") or "")
                    url = a.get("download_url") or a.get("url") or ""
                    if not url and isinstance(a.get("image_urls"), dict):
                        url = a["image_urls"].get("original") or a["image_urls"].get("default") or ""
                    _tail, ext = tail_ext(url) if isinstance(url, str) and url else ("", "")
                    name_ext = ""
                    if isinstance(fname, str) and "." in fname:
                        name_ext = "." + fname.rsplit(".", 1)[-1].lower()[:12]
                    media_name_ext[(rel_name, name_ext or "none", ext or "none", (mime[:24] or "none"), bool(fname))] += 1
                    if not fname and rel_name in ("media", "images", "attachments", "attachments_media", "audio"):
                        bare[rel_name + " mime=" + (mime[:24] or "none") + " urlext=" + (ext or "none") + " keys=" + ",".join(sorted(a.keys())[:8])] += 1
                    if is_reward and name_ext and name_ext not in IMG:
                        reward_included_nonimg.append((pid, rel_name, str(fname)[:80], mime))
                    if rel_name == "images" and len(image_attr_examples) < 3:
                        image_attr_examples.append((pid, sorted(a.keys()), str(fname)[:40], mime, ext))

    print("POSTS", n_posts)
    print("CONTENT", content_states)
    print("empty viewable reward rels", empty_reward)
    print("--- reward rel ---")
    for key, count in reward_rel.most_common(12):
        print(count, key)
    print("--- other viewable rel ---")
    for key, count in other_view_rel.most_common(8):
        print(count, key)
    print("--- post_file shapes ---")
    for key, count in post_file_shapes.most_common(20):
        print(count, key)
    print("--- post_file examples ---")
    for row in post_file_examples:
        print(row)
    print("--- embed ---")
    for key, count in embed_shapes.most_common(8):
        print(count, key)
    print("--- json nonimg ---")
    for key, count in json_nonimg.most_common(40):
        print(count, key)
    print("--- json noext hosts ---")
    for key, count in json_noext.most_common(20):
        print(count, key)
    print("--- html hosts ---")
    for key, count in html_hosts.most_common(15):
        print(count, key)
    print("--- year reward links ---")
    for year in sorted(year_reward):
        print("YEAR", year)
        for key, count in year_reward[year].most_common(15):
            print(" ", count, key)
    print("--- year other nonimg ---")
    for year in sorted(year_other_nonimg):
        print("YEAR", year)
        for key, count in year_other_nonimg[year].most_common(10):
            print(" ", count, key)
    print("--- nonimg reward examples ---")
    for row in nonimg_examples:
        print(row)
    print("--- bare ---")
    for key, count in bare.most_common(25):
        print(count, key)
    print("--- media name ext ---")
    for key, count in media_name_ext.most_common(30):
        print(count, key)
    print("reward included nonimg", len(reward_included_nonimg))
    for row in reward_included_nonimg[:20]:
        print(row)
    print("--- image attr ---")
    for row in image_attr_examples:
        print(row)

    # Compare list vs single for viewable posts: content, post_file, rel counts, link hosts.
    differ_content = 0
    differ_json_hosts = Counter()
    single_only_hosts = Counter()
    single_reward_nonimg = []
    single_post_file = Counter()
    compared = 0
    missing_single = 0
    plain_vs_inc = Counter()
    single_rel_reward = Counter()
    for pid, info in list_by_id.items():
        if not info["can"]:
            continue
        inc_path = SINGLE / ("post_%s_inc.json" % pid)
        plain_path = SINGLE / ("post_%s_plain.json" % pid)
        if not inc_path.is_file():
            missing_single += 1
            continue
        compared += 1
        inc = json.loads(inc_path.read_text(encoding="utf-8"))
        data = inc.get("data")
        if isinstance(data, list):
            data = data[0] if data else {}
        attrs = (data or {}).get("attributes") or {}
        if (info["content"] or None) != (attrs.get("content") or None):
            # both null/empty count as same
            left = info["content"] or ""
            right = attrs.get("content") or ""
            if left != right:
                differ_content += 1
        counts = rel_counts(data or {})
        sig = "|".join("%s=%s" % item for item in sorted(counts.items()))
        if info["reward"]:
            single_rel_reward[sig] += 1
            if sig != info["sig"]:
                plain_vs_inc["list!=inc-rel"] += 1
        post_file = attrs.get("post_file")
        if post_file is None:
            single_post_file["null"] += 1
        elif isinstance(post_file, dict):
            name = post_file.get("name") or ""
            url = post_file.get("url") or ""
            _tail, ext = tail_ext(url) if url else ("", "")
            single_post_file[(bool(name), ext or "none", str(post_file.get("mimetype") or "")[:20] or "none")] += 1
        jlinks = []
        blob = attrs.get("content_json_string")
        if isinstance(blob, str) and blob:
            try:
                collect_links(json.loads(blob), jlinks)
            except Exception:
                pass
        # also HTML content links
        if isinstance(attrs.get("content"), str):
            collect_links(attrs.get("content"), jlinks)
        for kind, url in jlinks:
            host = host_of(url)
            tail, ext = tail_ext(url)
            if ext in IMG:
                continue
            single_only_hosts[host + "|" + (ext or "noext")] += 1
            if info["reward"] and len(single_reward_nonimg) < 20:
                single_reward_nonimg.append((pid, info["year"], kind, host, ext or "noext", tail, info["title"][:40]))
        if plain_path.is_file() and info["reward"] and compared < 5:
            pass
        if info["reward"] and plain_path.is_file():
            plain = json.loads(plain_path.read_text(encoding="utf-8"))
            pdata = plain.get("data")
            if isinstance(pdata, list):
                pdata = pdata[0] if pdata else {}
            psig = "|".join("%s=%s" % item for item in sorted(rel_counts(pdata or {}).items()))
            if psig != sig:
                plain_vs_inc["plain!=inc"] += 1
            else:
                plain_vs_inc["plain==inc-rel"] += 1
            pblob = ((pdata or {}).get("attributes") or {}).get("content_json_string")
            iblob = attrs.get("content_json_string")
            if (pblob or "") != (iblob or ""):
                plain_vs_inc["json-diff"] += 1
            else:
                plain_vs_inc["json-same"] += 1

    print("COMPARED viewable singles", compared, "missing", missing_single, "content diff", differ_content)
    print("plain vs inc", plain_vs_inc)
    print("--- single reward rel ---")
    for key, count in single_rel_reward.most_common(12):
        print(count, key)
    print("--- single post_file ---")
    for key, count in single_post_file.most_common(15):
        print(count, key)
    print("--- single nonimg hosts ---")
    for key, count in single_only_hosts.most_common(30):
        print(count, key)
    print("--- single reward nonimg examples ---")
    for row in single_reward_nonimg:
        print(row)
    differ_json_hosts  # silence


def deep():
    from datetime import datetime

    IMG = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

    def local_year(iso):
        if not iso:
            return None
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().year
        except Exception:
            return None

    def path_prefix(url):
        rest = url.split("://", 1)[-1]
        path = rest.split("?", 1)[0]
        parts = path.split("/")
        # host/a/b
        if len(parts) >= 3:
            return "/".join(parts[1:3])
        return path[:40]

    years = {y: Counter() for y in range(2019, 2027)}
    preview_meta = Counter()
    drop_prefix = Counter()
    patreon_prefix = Counter()
    other_hosts = Counter()
    viewable_attach_media = []
    reward_examples = {y: None for y in range(2019, 2027)}
    file_bug = []
    locked_zip_posts = []

    for page in sorted(RAW.glob("*.json")):
        payload = json.loads(page.read_text(encoding="utf-8"))
        included = index_included(payload)
        for resource in payload.get("data") or []:
            attrs = resource.get("attributes") or {}
            pid = str(resource.get("id"))
            title = (attrs.get("title") or "").strip()
            year = local_year(attrs.get("published_at") or "")
            if year not in years:
                continue
            can = bool(attrs.get("current_user_can_view"))
            low = title.lower()
            kind = "other"
            if "reward" in low:
                kind = "reward"
            years[year]["posts"] += 1
            years[year]["viewable" if can else "locked"] += 1
            if kind == "reward":
                years[year]["reward_" + ("view" if can else "lock")] += 1
            meta = attrs.get("attachments_preview_metadata") or []
            if isinstance(meta, list) and meta:
                names = []
                for item in meta:
                    if isinstance(item, dict):
                        names.append(str(item.get("file_name") or ""))
                sig = "view" if can else "lock"
                preview_meta[(sig, kind, tuple(names)[:4])] += 1
                if any(name.lower().endswith(".zip") for name in names):
                    locked_zip_posts.append((pid, year, can, title[:60], names, page.name))
            blob = attrs.get("content_json_string") or ""
            links = []
            if isinstance(blob, str) and blob.startswith("{"):
                try:
                    collect_links(json.loads(blob), links)
                except Exception:
                    pass
            hosts = set()
            drop_n = 0
            for _k, url in links:
                host = host_of(url)
                _tail, ext = tail_ext(url)
                if host == "dropbox.com":
                    drop_n += 1
                    drop_prefix[path_prefix(url)] += 1
                elif "patreon.com" in host:
                    patreon_prefix[path_prefix(url)] += 1
                elif ext not in IMG:
                    other_hosts[host] += 1
                hosts.add(host)
            if can and kind == "reward":
                years[year]["reward_view_drop" if "dropbox.com" in hosts else "reward_view_nodrop"] += 1
                if reward_examples[year] is None:
                    reward_examples[year] = (pid, title[:70], page.name, sorted(hosts))
            # non-image included with a url, viewable
            if can:
                for rel_name, rel in (resource.get("relationships") or {}).items():
                    data = (rel or {}).get("data")
                    items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
                    for ref in items:
                        if not isinstance(ref, dict):
                            continue
                        item = included.get((str(ref.get("type") or ""), str(ref.get("id"))))
                        if not item:
                            continue
                        a = item.get("attributes") or {}
                        fname = str(a.get("file_name") or a.get("name") or "")
                        mime = str(a.get("mimetype") or "")
                        url = a.get("download_url") or a.get("url") or ""
                        _tail, ext = tail_ext(url) if isinstance(url, str) else ("", "")
                        name_ext = ""
                        if "." in fname:
                            name_ext = "." + fname.rsplit(".", 1)[-1].lower()
                        is_img = mime.startswith("image/") or name_ext in IMG or ext in IMG
                        if not is_img and (fname or mime or url):
                            viewable_attach_media.append((pid, year, rel_name, fname[:60], mime[:30], bool(url), a.get("owner_relationship"), title[:40]))
            post_file = attrs.get("post_file")
            if can and isinstance(post_file, dict):
                url = str(post_file.get("url") or "")
                name = post_file.get("name")
                _tail, ext = tail_ext(url)
                if ext in IMG and not name:
                    file_bug.append((pid, year, title[:40], ext, page.name))

    print("--- LOCAL YEAR ---")
    for year in range(2020, 2027):
        print(year, dict(years[year]))
    print("2019", dict(years[2019]))
    print("--- preview meta (top) ---")
    for key, count in preview_meta.most_common(20):
        print(count, key)
    print("--- drop prefixes ---")
    for key, count in drop_prefix.most_common(15):
        print(count, key)
    print("--- patreon prefixes ---")
    for key, count in patreon_prefix.most_common(15):
        print(count, key)
    print("--- other nonimg hosts ---")
    print(other_hosts)
    print("--- reward examples ---")
    for year, row in reward_examples.items():
        print(year, row)
    print("file bug viewable post_file image nameless", len(file_bug))
    print("by year", Counter(row[1] for row in file_bug))
    print("sample", file_bug[:3], file_bug[-1] if file_bug else None)
    print("--- viewable nonimage media ---")
    print("count", len(viewable_attach_media))
    for row in viewable_attach_media[:30]:
        print(row)
    print("--- zip preview posts ---")
    for row in locked_zip_posts:
        print(row)

    # manifest bare names
    man = Path("F:/Dropbox/TheCourt/research/patreon-downloader/sync-partyoftwo_20260929.json")
    if man.is_file():
        doc = json.loads(man.read_text(encoding="utf-8"))
        posts = doc.get("posts") or doc.get("creators") or []
        # shape
        print("manifest keys", list(doc.keys())[:20])
        bare = []
        kinds = Counter()
        reasons = Counter()

        def walk_posts(node):
            if isinstance(node, dict) and "post_id" in node and "files" in node:
                yield node
            elif isinstance(node, dict):
                for value in node.values():
                    yield from walk_posts(value)
            elif isinstance(node, list):
                for value in node:
                    yield from walk_posts(value)

        for post in walk_posts(doc):
            reasons[str(post.get("status")) + "|" + str(post.get("reason"))[:40]] += 1
            for f in post.get("files") or []:
                kinds[str(f.get("kind")) + "|" + str(f.get("name"))] += 0
                name = str(f.get("name") or "")
                kinds[str(f.get("kind"))] += 1
                if name == "file" or "." not in name:
                    bare.append((post.get("post_id"), post.get("post_title", "")[:40], name, f.get("kind"), f.get("status")))
        print("manifest reasons")
        for key, count in reasons.most_common(15):
            print(count, key)
        print("manifest kinds", kinds)
        print("bare count", len(bare))
        print("bare sample", bare[:8])


def cite():
    want = {"45652282", "32754303", "171028257", "147126133", "38814268"}
    for page in sorted(RAW.glob("*.json")):
        payload = json.loads(page.read_text(encoding="utf-8"))
        for resource in payload.get("data") or []:
            pid = str(resource.get("id"))
            if pid not in want:
                continue
            attrs = resource.get("attributes") or {}
            blob = attrs.get("content_json_string") or ""
            links = []
            if blob:
                collect_links(json.loads(blob), links)
            print("POST", pid, "page", page.name, "title", (attrs.get("title") or "")[:70])
            print(" content is", type(attrs.get("content")).__name__, "post_file", attrs.get("post_file"))
            seen = set()
            for kind, url in links:
                if url in seen:
                    continue
                seen.add(url)
                host = host_of(url)
                tail, ext = tail_ext(url)
                path = url.split("?", 1)[0]
                print(" ", kind, host, ext or "noext", path[:140])
            single = SINGLE / ("post_%s_inc.json" % pid)
            print(" single", single.is_file())
            if single.is_file():
                inc = json.loads(single.read_text(encoding="utf-8"))
                data = inc.get("data")
                shape = type(data).__name__
                if isinstance(data, list):
                    data = data[0] if data else {}
                sattrs = (data or {}).get("attributes") or {}
                slinks = []
                sblob = sattrs.get("content_json_string") or ""
                if sblob:
                    collect_links(json.loads(sblob), slinks)
                shosts = sorted({host_of(url) for _k, url in slinks})
                print(" single data", shape, "content", type(sattrs.get("content")).__name__, "hosts", shosts)
                print(" json same", (sblob or "") == (blob or ""))


def _doc(links):
    content = []
    for label, url, text in links:
        content.append({
            "type": "paragraph",
            "content": [
                {"type": "text", "text": label},
                {
                    "type": "text",
                    "marks": [{"type": "link", "attrs": {"href": url, "target": "_blank"}}],
                    "text": text,
                },
            ],
        })
    return json.dumps({"type": "doc", "content": content}, ensure_ascii=False)


def write_fixtures():
    root = Path("C:/Users/DougJ/Documents/GitHub/local-asset-management/tests/fixtures/patreon")
    u4 = "https://www.dropbox.com/scl/fo/oq7b4f0d3injctpzq3vwa/ADH8eb5zQJ_ph5ymclI-Qy0?<query-redacted>"
    u5 = "https://www.dropbox.com/scl/fo/tiq5vjzp50ovwiq5pnnsi/ACm4m2kK9n4t9w64u5jPcv0?<query-redacted>"
    u6 = "https://www.dropbox.com/scl/fo/d0gdf5j3y9uos48fmrqia/ADOF5jGTL0p8nmRdSxzJ0Vs?<query-redacted>"
    master = "https://www.patreon.com/posts/30364003"
    sh10 = "https://www.dropbox.com/sh/d9ubgf5uwksjzsy/AABRs4HSVimvMWgBSPLEMT_wa?<query-redacted>"
    sh11 = "https://www.dropbox.com/sh/d34n2dx32ibjis0/AACH6Jt6bzzUDQwjk6vrOyCMa?<query-redacted>"
    sh12 = "https://www.dropbox.com/sh/pozrppry9lccu8r/AABfoHR9u3KckqporEZvXJyHa?<query-redacted>"
    rewards = {
        "data": [
            {
                "id": "171028257",
                "type": "post",
                "attributes": {
                    "title": "Magic Worlds Set 4-6 : $5 Rewards ",
                    "url": "https://www.patreon.com/partyoftwo/posts/magic-worlds-set-171028257",
                    "published_at": "2026-09-30T18:35:58.000+00:00",
                    "current_user_can_view": True,
                    "content": None,
                    "post_file": None,
                    "post_type": "text_only",
                    "is_paid": False,
                    "embed": None,
                    "content_json_string": _doc([
                        ("Set 4 : ", u4, "Folder"),
                        ("Set 4b : ", u4, "Direct download"),
                        ("Set 5 : ", u5, "Folder"),
                        ("Set 6 : ", u6, "Direct download"),
                        ("", master, "Masterpost of all $5 sets"),
                    ]),
                },
                "relationships": {
                    "attachments": {"data": []},
                    "attachments_media": {"data": []},
                    "audio": {"data": None},
                    "images": {"data": []},
                    "media": {"data": []},
                },
            },
            {
                "id": "45652282",
                "type": "post",
                "attributes": {
                    "title": "Aztec Temple Set 10-12 : $5 Reward Post",
                    "url": "https://www.patreon.com/partyoftwo/posts/aztec-temple-set-45652282",
                    "published_at": "2020-12-31T22:46:15.000+00:00",
                    "current_user_can_view": True,
                    "content": None,
                    "post_file": None,
                    "post_type": "text_only",
                    "is_paid": False,
                    "embed": None,
                    "content_json_string": _doc([
                        ("Set 10 : ", sh10, "Folder"),
                        ("Set 10b : ", sh10, "Direct Download"),
                        ("Set 11 : ", sh11, "Folder"),
                        ("Set 12 : ", sh12, "Direct Download"),
                        ("", master, "Archive of all previous sets"),
                    ]),
                },
                "relationships": {
                    "attachments": {"data": []},
                    "attachments_media": {"data": []},
                    "audio": {"data": None},
                    "images": {"data": []},
                    "media": {"data": []},
                },
            },
        ],
        "included": [],
        "links": {},
    }
    cover_url = "https://c10.patreonusercontent.com/4/patreon-media/p/post/168120643/b671bcdd8d95431cab3ed2d6807a273d/eyJ3Ijo2MjB9/1.jpg?<query-redacted>"
    image_url = "https://c10.patreonusercontent.com/4/patreon-media/p/post/168120643/de383a5920324c73bdbd265f9b03ca25/eyJhIjoxLCJwIjoxfQ%3D%3D/1.jpg?<query-redacted>"
    cover = {
        "data": [
            {
                "id": "168120643",
                "type": "post",
                "attributes": {
                    "title": "Magic Worlds - Set 1",
                    "url": "https://www.patreon.com/partyoftwo/posts/magic-worlds-set-168120643",
                    "published_at": "2026-08-30T18:31:35.000+00:00",
                    "current_user_can_view": True,
                    "content": None,
                    "post_type": "image_file",
                    "post_file": {
                        "url": cover_url,
                        "width": 620,
                        "height": 400,
                        "state": "ready",
                    },
                },
                "relationships": {
                    "attachments": {"data": []},
                    "attachments_media": {"data": []},
                    "images": {"data": [{"id": "img-bridge", "type": "media"}]},
                    "media": {"data": [{"id": "img-bridge", "type": "media"}]},
                },
            }
        ],
        "included": [
            {
                "id": "img-bridge",
                "type": "media",
                "attributes": {
                    "file_name": "Online_Bridge of the Stars_Day_Gridless.jpg",
                    "download_url": image_url,
                    "mimetype": "image/jpeg",
                    "size_bytes": 1200,
                    "owner_relationship": "inline",
                },
            }
        ],
        "links": {},
    }
    locked = {
        "data": [
            {
                "id": "147126125",
                "type": "post",
                "attributes": {
                    "title": "Infinite Tower 7-9 : $1 Rewards Post ",
                    "url": "https://www.patreon.com/partyoftwo/posts/infinite-tower-7-147126125",
                    "published_at": "2026-01-01T03:09:44.000+00:00",
                    "current_user_can_view": False,
                    "content": None,
                    "post_file": None,
                    "post_type": "text_only",
                    "attachments_preview_metadata": [
                        {
                            "file_name": "1_InfiniteTower_Set7.zip",
                            "mimetype": "application",
                            "size_bytes": 176691830,
                        }
                    ],
                },
                "relationships": {
                    "audio": {"data": None},
                    "images": {"data": []},
                    "media": {"data": [{"id": "589526183", "type": "media"}]},
                },
            }
        ],
        "included": [
            {
                "id": "589526183",
                "type": "media",
                "attributes": {
                    "file_name": "1_InfiniteTower_Set7.zip",
                    "mimetype": "application/zip",
                    "owner_id": "147126125",
                    "owner_relationship": "attachment",
                    "size_bytes": 176691830,
                    "state": "ready",
                },
            }
        ],
        "links": {},
    }
    (root / "partyoftwo-rewards-5.json").write_text(json.dumps(rewards, indent=2) + "\n", encoding="utf-8")
    (root / "partyoftwo-post-file-cover.json").write_text(json.dumps(cover, indent=2) + "\n", encoding="utf-8")
    (root / "partyoftwo-locked-zip.json").write_text(json.dumps(locked, indent=2) + "\n", encoding="utf-8")
    print("wrote fixtures")


if __name__ == "__main__":
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        deep()
        cite()
    text = buf.getvalue()
    keep = (
        "manifest reasons",
        "bare count",
        "zip preview",
        "deferred",
        "downloaded|",
        "locked|",
    )
    for line in text.splitlines():
        if len(line) > 240:
            continue
        if any(bit in line for bit in keep):
            print(line)
