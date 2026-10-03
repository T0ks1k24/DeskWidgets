#!/usr/bin/env python3
"""Anime widget data from AniList (public GraphQL API, no key needed).

Usage: fetch.py CACHE COVERS_DIR MODE [USERNAME] [TITLE_LANG]
  MODE        airing    popular shows airing now, ordered by the next episode
              list      the "Watching" list of USERNAME on AniList
              trending  what is trending right now
  TITLE_LANG  romaji | english | native
Writes  aN=airingAt|episode|episodes|title|cover.png|url|progress  lines.
Covers are cached as PNG (via GdkPixbuf or Pillow). On any error the old cache is kept.
"""
import json
import os
import sys
import urllib.request

API = 'https://graphql.anilist.co'
MEDIA = 'id siteUrl episodes title { romaji english native } coverImage { medium } nextAiringEpisode { airingAt episode }'
QUERIES = {
    'airing': 'query { Page(page: 1, perPage: 16) { media(status: RELEASING, type: ANIME, sort: POPULARITY_DESC, isAdult: false) { %s } } }' % MEDIA,
    'trending': 'query { Page(page: 1, perPage: 12) { media(type: ANIME, sort: TRENDING_DESC, isAdult: false) { %s } } }' % MEDIA,
    'list': 'query ($user: String) { MediaListCollection(userName: $user, type: ANIME, status_in: [CURRENT, REPEATING]) '
            '{ lists { entries { progress media { %s } } } } }' % MEDIA,
}


def post(query, variables):
    body = json.dumps({'query': query, 'variables': variables}).encode()
    req = urllib.request.Request(API, data=body, headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                                                          'User-Agent': 'deskwidgets/2.0'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    if data.get('errors'):
        raise RuntimeError(data['errors'][0].get('message'))
    return data['data']


def to_png(data, out):
    try:
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        loader.get_pixbuf().savev(out, 'png', [], [])
        return True
    except Exception:
        pass
    try:
        from io import BytesIO
        from PIL import Image
        Image.open(BytesIO(data)).convert('RGBA').save(out)
        return True
    except Exception:
        return False


def cover(url, media_id, covers):
    if not url:
        return ''
    out = os.path.join(covers, f'{media_id}.png')
    if os.path.exists(out):
        return out
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'deskwidgets/2.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read(4 << 20)
        if to_png(data, out + '.tmp'):
            os.replace(out + '.tmp', out)
            return out
    except Exception:
        pass
    return ''


def main():
    cache, covers, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    user = sys.argv[4] if len(sys.argv) > 4 else ''
    lang = sys.argv[5] if len(sys.argv) > 5 else 'romaji'
    if mode == 'list' and not user:
        mode = 'airing'
    os.makedirs(covers, exist_ok=True)
    data = post(QUERIES[mode], {'user': user} if mode == 'list' else {})

    items = []
    if mode == 'list':
        for lst in data['MediaListCollection']['lists']:
            for entry in lst['entries']:
                items.append((entry['media'], entry.get('progress') or 0))
    else:
        items = [(m, 0) for m in data['Page']['media']]

    rows = []
    for media, progress in items:
        nxt = media.get('nextAiringEpisode') or {}
        title = (media['title'].get(lang) or media['title'].get('romaji') or '').replace('|', '/').replace('\n', ' ')
        rows.append((nxt.get('airingAt') or 0, nxt.get('episode') or 0, media.get('episodes') or 0, title,
                     media['coverImage'].get('medium'), media['id'], media.get('siteUrl') or '', progress))
    if mode != 'trending':   # soonest episode first, finished/unknown at the end
        rows.sort(key=lambda r: (r[0] == 0, r[0]))
    rows = rows[:10]

    lines = [f'mode={mode}', f'count={len(rows)}']
    for i, (at, ep, total, title, cover_url, media_id, url, progress) in enumerate(rows, 1):
        lines.append(f'a{i}={at}|{ep}|{total}|{title}|{cover(cover_url, media_id, covers)}|{url}|{progress}')
    tmp = cache + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(tmp, cache)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'anime fetch: {e}', file=sys.stderr)
