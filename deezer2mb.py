#!/usr/bin/env python3
"""
mb_batch_submit.py — Generic MusicBrainz batch submitter

Usage:
  python3 mb_batch_submit.py <deezer_url>                  # setup + submit all MISSING
  python3 mb_batch_submit.py <deezer_url> --setup-only     # fetch discography + MB check only
  python3 mb_batch_submit.py <json_path>                   # resume from saved JSON
  python3 mb_batch_submit.py <json_path> --start N         # resume from index N
  python3 mb_batch_submit.py <json_path> --limit N         # submit at most N releases
  python3 mb_batch_submit.py <json_path> --recheck         # re-verify UNCERTAIN/FAILED against MB API
"""

from playwright.sync_api import sync_playwright
import os, json, urllib.request, urllib.parse, time, re, sys, argparse

STATE_PATH = os.path.expanduser("~/.config/musicbrainz/browser_state.json")
DELAY_BETWEEN_RELEASES = 12  # seconds between submissions

_contact = os.environ.get("MB_CONTACT_EMAIL", "your-email@example.com")
UA = f"mb-batch-submit/1.0 ({_contact})"

MBID_CACHE = {}  # populated dynamically via mb_search_artist()


# ── Deezer helpers ────────────────────────────────────────────────────────────

def deezer_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def resolve_deezer_artist(deezer_input):
    """Return (artist_id, artist_name) from a Deezer artist URL or numeric ID."""
    m = re.search(r'deezer\.com/(?:[a-z]+/)?artist/(\d+)', deezer_input)
    if m:
        artist_id = m.group(1)
    elif deezer_input.isdigit():
        artist_id = deezer_input
    else:
        raise ValueError(f"Cannot parse Deezer artist input: {deezer_input!r}")
    data = deezer_get(f"https://api.deezer.com/artist/{artist_id}")
    return artist_id, data['name']


def fetch_deezer_discography(artist_id):
    """Fetch all albums/EPs/singles for a Deezer artist, sorted by release_date."""
    url = f"https://api.deezer.com/artist/{artist_id}/albums?limit=100"
    items = []
    while url:
        data = deezer_get(url)
        items.extend(data.get('data', []))
        url = data.get('next')
    keep = [a for a in items if a.get('record_type') in ('album', 'ep', 'single')]
    keep.sort(key=lambda a: a.get('release_date') or '')
    return keep


def fetch_deezer_album(album_id):
    return deezer_get(f"https://api.deezer.com/album/{album_id}")


# ── MusicBrainz helpers ───────────────────────────────────────────────────────

def mb_get(path, params=None):
    url = f"https://musicbrainz.org/ws/2/{path}"
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def mb_search_artist(name):
    if name in MBID_CACHE:
        return MBID_CACHE[name]
    data = mb_get('artist', {'query': name, 'limit': 5, 'fmt': 'json'})
    hits = [a for a in data.get('artists', [])
            if a['name'].lower() == name.lower() and a['score'] >= 80]
    mbid = hits[0]['id'] if hits else None
    MBID_CACHE[name] = mbid
    time.sleep(1.1)
    return mbid


_LUCENE_SPECIAL = re.compile(r'([\+\-\&\|\!\(\)\{\}\[\]\^\”\~\*\?\:\/\\])')

def lucene_escape(s):
    return _LUCENE_SPECIAL.sub(r'\\\1', s)


def normalize_title(t):
    return t.lower().strip().replace('‘', "'").replace('’', "'").replace('“', '"').replace('”', '"')


def mb_check_release(title, artist_name):
    """Return (status, mb_url) — EXISTS / UNCERTAIN / MISSING."""
    data = mb_get('release', {
        'query': f"release:{lucene_escape(title)} AND artistname:{lucene_escape(artist_name)}",
        'limit': 3,
        'fmt': 'json',
    })
    hits = data.get('releases', [])
    if not hits:
        return 'MISSING', None
    top = hits[0]
    mb_url = f"https://musicbrainz.org/release/{top['id']}"
    if top['score'] >= 90 and normalize_title(top['title']) == normalize_title(title):
        return 'EXISTS', mb_url
    if top['score'] >= 70:
        return 'UNCERTAIN', mb_url
    return 'MISSING', None


# ── Discography setup ─────────────────────────────────────────────────────────

def slugify(name):
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def setup_discography(deezer_input):
    """
    Fetch Deezer discography + check MB for each release.
    Returns (disc_path, artist_name, artist_mbid).
    Saves JSON to /tmp/<slug>_discography.json.
    """
    artist_id, artist_name = resolve_deezer_artist(deezer_input)
    print(f"Artist: {artist_name} (Deezer ID {artist_id})")

    print("Fetching Deezer discography...")
    albums = fetch_deezer_discography(artist_id)
    print(f"  Found {len(albums)} releases")

    print("Looking up artist on MusicBrainz...")
    artist_mbid = mb_search_artist(artist_name)
    if artist_mbid:
        print(f"  MB MBID: {artist_mbid}")
    else:
        print(f"  WARNING: artist not found on MB — will auto-create if needed")

    print("Checking MB for each release (this takes a moment)...")
    entries = []
    for alb in albums:
        status, mb_url = mb_check_release(alb['title'], artist_name)
        entry = {
            'id': alb['id'],
            'title': alb['title'],
            'record_type': alb.get('record_type', 'single'),
            'release_date': alb.get('release_date', ''),
            'artist_name': artist_name,
            'mb_status': status,
        }
        if mb_url:
            entry['mb_url'] = mb_url
        entries.append(entry)
        mark = '✓' if status == 'EXISTS' else ('?' if status == 'UNCERTAIN' else '✗')
        print(f"  {mark} {alb['release_date']} | {alb['record_type']:7} | {alb['title']}")
        time.sleep(1.1)

    disc_path = f"/tmp/{slugify(artist_name)}_discography.json"
    with open(disc_path, 'w') as f:
        json.dump(entries, f, indent=2)

    exists = sum(1 for e in entries if e['mb_status'] == 'EXISTS')
    missing = sum(1 for e in entries if e['mb_status'] in ('MISSING', 'UNCERTAIN'))
    print(f"\nSaved to {disc_path}")
    print(f"EXISTS: {exists}  |  MISSING/UNCERTAIN: {missing}")
    return disc_path, artist_name, artist_mbid


# ── Playwright helpers ────────────────────────────────────────────────────────

def autocomplete_select(page, css_sel, mbid, label):
    """Type an MBID into an MB autocomplete field, wait for dropdown, click first result."""
    page.locator(css_sel).click(click_count=3)
    page.wait_for_timeout(100)
    page.keyboard.type(mbid, delay=20)
    try:
        page.wait_for_selector('ul.ui-autocomplete li.ui-menu-item', state='visible', timeout=6000)
        page.wait_for_timeout(300)
        page.locator('ul.ui-autocomplete li.ui-menu-item').first.click()
    except Exception:
        pass
    page.wait_for_timeout(1500)
    bg = page.evaluate(
        f"() => window.getComputedStyle(document.querySelector({json.dumps(css_sel)})).backgroundColor"
    )
    if 'rgb(177' not in bg:
        raise RuntimeError(f"{label} not resolved (bg={bg})")


# ── Artist / RG creation ──────────────────────────────────────────────────────

def create_rg(ctx, title, mb_type, primary_artist_mbid):
    rg_page = ctx.new_page()
    rg_page.goto("https://musicbrainz.org/release-group/create")
    rg_page.wait_for_load_state("networkidle", timeout=15000)
    rg_page.wait_for_timeout(2000)
    rg_page.locator('input[name="edit-release-group.name"]').first.fill(title)
    type_map = {'album': 'Album', 'single': 'Single', 'ep': 'EP'}
    rg_page.locator('select[name="edit-release-group.primary_type_id"]').select_option(
        label=type_map.get(mb_type, 'Single')
    )
    autocomplete_select(rg_page, '#ac-source-single-artist', primary_artist_mbid, 'RG artist')
    rg_page.locator('button.submit.positive', has_text="Enter edit").click()
    try:
        rg_page.wait_for_url(lambda url: '/release-group/create' not in url, timeout=12000)
        m = re.search(r'/release-group/([0-9a-f-]{36})', rg_page.url)
        rg_mbid = m.group(1) if m else None
        rg_page.close()
        return rg_mbid
    except Exception as e:
        errors = rg_page.locator('.flash-error, .error').all_inner_texts()
        print(f"    RG failed ({e}): {errors[:2]}")
        rg_page.close()
        return None


def create_artist(ctx, name):
    artist_page = ctx.new_page()
    artist_page.goto("https://musicbrainz.org/artist/create")
    artist_page.wait_for_load_state("networkidle", timeout=15000)
    artist_page.wait_for_timeout(2000)
    artist_page.locator('input[name="edit-artist.name"]').fill(name)
    artist_page.locator('input[name="edit-artist.sort_name"]').fill(name)
    artist_page.locator('button.submit.positive', has_text="Enter edit").click()
    try:
        artist_page.wait_for_url(lambda url: '/artist/create' not in url, timeout=12000)
        m = re.search(r'/artist/([0-9a-f-]{36})', artist_page.url)
        artist_mbid = m.group(1) if m else None
        artist_page.close()
        if artist_mbid:
            MBID_CACHE[name] = artist_mbid
        return artist_mbid
    except Exception as e:
        errors = artist_page.locator('.flash-error, .error').all_inner_texts()
        print(f"    Artist creation failed ({e}): {errors[:2]}")
        artist_page.close()
        return None


# ── Artist credit builder ─────────────────────────────────────────────────────

def build_artists(album_data):
    contributors = album_data.get('contributors', [])
    main = [c for c in contributors if c.get('role') == 'Main']
    featured = [c for c in contributors if c.get('role') == 'Featured']
    if not main:
        main = [album_data.get('artist', {'name': 'Unknown'})]
    artists = []
    all_ordered = main + featured
    for i, artist in enumerate(all_ordered):
        name = artist['name']
        mbid = mb_search_artist(name)
        is_last = (i == len(all_ordered) - 1)
        if i == len(main) - 1 and featured:
            join = ' feat. '
        elif i > len(main) - 1 and not is_last:
            join = ' & '
        else:
            join = ''
        artists.append({'name': name, 'mbid': mbid, 'join': join})
    return artists


def build_tracks(album_data):
    return [{'name': t['title'], 'ms': t['duration'] * 1000}
            for t in album_data.get('tracks', {}).get('data', [])]


def track_parser_text(tracks):
    lines = []
    for i, t in enumerate(tracks):
        mm = t['ms'] // 60000
        ss = (t['ms'] % 60000) // 1000
        lines.append(f"{i+1}. {t['name']} ({mm}:{ss:02d})")
    return '\n'.join(lines)


# ── Release submitter ─────────────────────────────────────────────────────────

def submit_release(page, title, mb_type, rg_mbid, artists, year, month, day,
                   deezer_url, tracks, edit_note):
    page.goto("https://musicbrainz.org/release/add")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(3000)
    if page.locator('#name').count() == 0:
        raise RuntimeError("SESSION_EXPIRED")

    # Use keyboard.type() (trusted CDP events) so KO's valueUpdate:'input' handler fires.
    # Playwright's fill() fires synthetic events that KO may ignore.
    page.locator('#name').click(click_count=3)
    page.keyboard.type(title, delay=20)

    autocomplete_select(page, '#release-group', rg_mbid, 'RG')

    # Single artist: #ac-source-single-artist (main form field, no panel).
    # Multi-artist: #open-ac-source panel. Do NOT use the panel for single-artist —
    # it leaves allowsSubmission() false.
    if len(artists) == 1:
        autocomplete_select(page, '#ac-source-single-artist', artists[0]['mbid'], f"artist '{artists[0]['name']}'")
    else:
        page.locator('#open-ac-source').click()
        page.wait_for_selector('#ac-source-artist-0', state='visible', timeout=8000)
        page.wait_for_timeout(500)
        for i, a in enumerate(artists):
            if i > 0:
                page.locator('button.add-item.with-label', has_text="Add artist credit").click()
                page.wait_for_selector(f'#ac-source-artist-{i}', state='visible', timeout=5000)
                page.wait_for_timeout(300)
            autocomplete_select(page, f'#ac-source-artist-{i}', a['mbid'], f"artist {i} '{a['name']}'")
            page.locator(f'#ac-source-join-phrase-{i}').fill(a['join'])
        page.locator('button[type="submit"].positive').click()
        page.wait_for_timeout(1000)

    page.locator('.partial-date-year').first.click(click_count=3)
    page.keyboard.type(str(year), delay=20)
    page.locator('.partial-date-month').first.click(click_count=3)
    page.keyboard.type(str(month), delay=20)
    page.locator('.partial-date-day').first.click(click_count=3)
    page.keyboard.type(str(day), delay=20)
    page.keyboard.press("Tab")
    page.wait_for_timeout(300)

    page.locator('input.value.with-button').first.click(click_count=3)
    page.keyboard.type(deezer_url, delay=5)
    page.keyboard.press("Tab")
    page.wait_for_timeout(800)

    # Set release status to Official (statusID=1 in MB numbering).
    # Without this, releases are submitted with status=None and Lidarr filters them
    # out if the metadata profile only allows Official releases.
    status_result = page.evaluate("""() => {
        const rel = MB._releaseEditor?.rootField?.release?.();
        if (rel && typeof rel.statusID === 'function') {
            rel.statusID(1);
            return 'ko';
        }
        return null;
    }""")
    if status_result != 'ko':
        # Fallback: find the Status select element by its options
        for sel in page.locator('select').all():
            opts = [o.inner_text().strip() for o in sel.locator('option').all()]
            if 'Official' in opts:
                sel.select_option(label='Official')
                status_result = 'select'
                break
    print(f"  Status set via: {status_result or 'NOT SET — check form'}")
    page.wait_for_timeout(300)

    page.locator('a[href="#tracklist"]').click()
    page.wait_for_timeout(1500)
    page.evaluate("""() => {
        document.querySelectorAll('[role="dialog"]').forEach(d => {
            const btn = d.querySelector('button.ui-dialog-titlebar-close');
            if (btn) btn.click();
        });
    }""")
    page.wait_for_timeout(500)

    fmt_sel = page.locator('select[id^="medium-format"]')
    if fmt_sel.count() > 0:
        fmt_sel.first.select_option(label="Digital Media")

    tp_btn = page.locator('button.open-track-parser')
    if tp_btn.count() > 0 and tracks:
        tp_btn.click()
        page.wait_for_timeout(800)
        parser_dlg = page.get_by_role("dialog", name="Track parser")
        parser_dlg.locator("textarea").fill(track_parser_text(tracks))
        parser_dlg.get_by_role("button", name="Parse tracks").click()
        page.wait_for_timeout(1500)
        page.evaluate("""() => {
            document.querySelectorAll('[role="dialog"]').forEach(d => {
                const t = d.querySelector('.ui-dialog-title');
                if (t && t.textContent.includes('Track parser')) {
                    const btn = d.querySelector('button.ui-dialog-titlebar-close');
                    if (btn) btn.click();
                }
            });
        }""")
        page.wait_for_timeout(500)

    # Click all visible confirmation checkboxes — MB shows these for ETI, feat in track titles,
    # miscapitalized titles, various-artists, early format, strange packaging, etc.
    # For beginner accounts every unconfirmed validator blocks allowsSubmission().
    for confirm_id in [
        'confirm-eti',
        'confirm-feat',
        'confirm-miscapitalized-titles',
        'confirm-various-artists',
        'confirm-early-format',
        'confirm-strange-packaging',
        'confirm-useless-medium-title',
    ]:
        cb = page.locator(f'#{confirm_id}')
        if cb.count() > 0 and cb.is_visible() and not cb.is_checked():
            cb.click()
            page.wait_for_timeout(300)

    page.locator('a[href="#edit-note"]').click()
    page.wait_for_timeout(2000)
    note = page.locator('textarea#edit-note-text')
    if note.count() > 0:
        note.click()
        page.wait_for_timeout(300)
        page.keyboard.type(edit_note)
        page.wait_for_timeout(500)

    debug = page.evaluate("""() => {
        const re = MB._releaseEditor;
        const r = re.rootField.release();
        const trueObservables = [];
        for (const k of Object.keys(r)) {
            try {
                if (typeof r[k] === 'function' && r[k]() === true) trueObservables.push(k);
            } catch(e) {}
        }
        return {
            errorsExist: re.validation.errorsExist(),
            allows: re.allowsSubmission(),
            missingNote: re.rootField.missingEditNote(),
            trueObservables,
        };
    }""")
    print(f"  DEBUG: errorsExist={debug['errorsExist']} allows={debug['allows']} missingNote={debug['missingNote']}")
    if debug['trueObservables']:
        print(f"  DEBUG true observables: {debug['trueObservables']}")

    enter_edit = page.locator("#enter-edit")
    if not enter_edit.is_enabled():
        errors = list(dict.fromkeys([
            e.strip() for e in page.locator(".field-error:visible").all_inner_texts()
            if 10 < len(e.strip()) < 200
        ]))
        raise RuntimeError(f"DISABLED: {errors[:3]}")

    enter_edit.click()
    try:
        page.wait_for_url(
            lambda url: bool(re.search(r'/release/[0-9a-f-]{36}', url)),
            timeout=30000
        )
        return page.url
    except Exception as e:
        raise RuntimeError(f"POST_SUBMIT_TIMEOUT: {page.url}")


# ── Session ───────────────────────────────────────────────────────────────────

def ensure_session():
    """Check session validity; open headed browser for re-login if expired."""
    if os.path.exists(STATE_PATH):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(storage_state=STATE_PATH)
            page = ctx.new_page()
            page.goto("https://musicbrainz.org/release/add")
            page.wait_for_load_state("networkidle", timeout=15000)
            logged_in = page.locator('#name').count() > 0
            browser.close()
        if logged_in:
            print("MB session: valid")
            return
        print("MB session: expired, re-logging in...")
    else:
        print("MB session: no session file found, logging in...")

    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto("https://musicbrainz.org/login")
        print("  Log in to MusicBrainz in the browser window, then wait...")
        page.wait_for_url(lambda url: 'login' not in url, timeout=120000)
        ctx.storage_state(path=STATE_PATH)
        browser.close()
    print("  Session saved.")


# ── Batch processor ───────────────────────────────────────────────────────────

def save_disc(disc_path, data):
    with open(disc_path, 'w') as f:
        json.dump(data, f, indent=2)


def batch_process(disc_path, start_idx=0, limit=None):
    with open(disc_path) as f:
        data = json.load(f)

    targets = [(i, r) for i, r in enumerate(data)
               if r.get('mb_status') in ('MISSING', 'UNCERTAIN', 'FAILED')]
    if start_idx:
        targets = [(i, r) for i, r in targets if i >= start_idx]
    if limit:
        targets = targets[:limit]

    print(f"Processing {len(targets)} releases from {disc_path}...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=STATE_PATH)
        page = ctx.new_page()

        submitted = 0
        failed = []

        for batch_n, (idx, release) in enumerate(targets):
            title = release['title']
            record_type = release.get('record_type', 'single')
            deezer_id = release['id']
            deezer_url = f"https://www.deezer.com/album/{deezer_id}"

            print(f"\n[{batch_n+1}/{len(targets)}] {title} (idx={idx}, type={record_type})")

            try:
                album_data = fetch_deezer_album(deezer_id)
                release_date = album_data.get('release_date', '')
                parts = release_date.split('-') if release_date else ['', '', '']
                year = parts[0] if len(parts) > 0 else ''
                month = parts[1] if len(parts) > 1 else ''
                day = parts[2] if len(parts) > 2 else ''

                artists = build_artists(album_data)

                for a in artists:
                    if not a['mbid']:
                        print(f"  Artist '{a['name']}' not on MB — creating...")
                        a['mbid'] = create_artist(ctx, a['name'])
                        if not a['mbid']:
                            raise RuntimeError(f"Could not create artist '{a['name']}' on MB")
                        print(f"  Created artist: {a['mbid']}")
                        time.sleep(2)

                for a in artists:
                    print(f"  artist: {a['name']} ({a['mbid']}) join='{a['join']}'")

                tracks = build_tracks(album_data)
                print(f"  tracks: {len(tracks)}, date: {year}-{month}-{day}")

                mb_type = {'album': 'album', 'ep': 'ep', 'single': 'single'}.get(record_type, 'single')
                edit_note = f"new {mb_type}"

                rg_mbid = release.get('rg_mbid')
                if not rg_mbid:
                    print(f"  Creating RG...")
                    rg_mbid = create_rg(ctx, title, mb_type, artists[0]['mbid'])
                    if not rg_mbid:
                        raise RuntimeError("RG_CREATION_FAILED")
                    with open(disc_path) as f:
                        data = json.load(f)
                    data[idx]['rg_mbid'] = rg_mbid
                    save_disc(disc_path, data)
                    print(f"  RG: {rg_mbid}")
                else:
                    print(f"  Reusing RG: {rg_mbid}")

                print(f"  Submitting...")
                result_url = submit_release(
                    page, title, mb_type, rg_mbid, artists,
                    year, month, day, deezer_url, tracks, edit_note
                )

                with open(disc_path) as f:
                    data = json.load(f)
                data[idx]['mb_status'] = 'EXISTS'
                data[idx]['mb_url'] = result_url
                save_disc(disc_path, data)

                submitted += 1
                print(f"  DONE: {result_url}")

                if batch_n < len(targets) - 1:
                    print(f"  Waiting {DELAY_BETWEEN_RELEASES}s...")
                    time.sleep(DELAY_BETWEEN_RELEASES)

            except RuntimeError as e:
                err = str(e)
                print(f"  FAILED: {err}")
                failed.append((idx, title, err))
                with open(disc_path) as f:
                    data = json.load(f)
                data[idx]['mb_status'] = 'FAILED'
                data[idx]['fail_reason'] = err
                save_disc(disc_path, data)
                if 'SESSION_EXPIRED' in err:
                    print("  Session expired — aborting batch. Re-login and resume with --start.")
                    break

            except Exception as e:
                print(f"  UNEXPECTED: {type(e).__name__}: {e}")
                failed.append((idx, title, str(e)))
                with open(disc_path) as f:
                    data = json.load(f)
                data[idx]['mb_status'] = 'FAILED'
                data[idx]['fail_reason'] = f"{type(e).__name__}: {e}"
                save_disc(disc_path, data)

        browser.close()

    print(f"\n{'='*50}")
    print(f"Submitted: {submitted}/{len(targets)}")
    if failed:
        print(f"Failed ({len(failed)}):")
        for idx, title, reason in failed:
            print(f"  [{idx}] {title}: {reason}")

    return submitted, failed


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generic MusicBrainz batch submitter")
    parser.add_argument('input', help='Deezer artist URL or path to existing discography JSON')
    parser.add_argument('--setup-only', action='store_true',
                        help='Fetch discography + check MB, but do not submit')
    parser.add_argument('--start', type=int, default=0, metavar='N',
                        help='Skip to index N in the discography (resume)')
    parser.add_argument('--limit', type=int, default=None, metavar='N',
                        help='Submit at most N releases')
    parser.add_argument('--mbid', action='append', metavar='NAME=UUID',
                        help='Pre-seed MBID cache for ambiguous artists, e.g. --mbid "Hostage=15d025bd-..."')
    parser.add_argument('--recheck', action='store_true',
                        help='Re-verify UNCERTAIN/FAILED releases against MB API before submitting')
    args = parser.parse_args()

    if args.mbid:
        for entry in args.mbid:
            name, mbid = entry.split('=', 1)
            MBID_CACHE[name] = mbid
            print(f"MBID pre-seeded: {name} → {mbid}")

    # Determine mode: resume from JSON or full setup from Deezer URL
    if args.input.endswith('.json') or os.path.isfile(args.input):
        disc_path = args.input
        print(f"Resuming from {disc_path}")
    else:
        disc_path, _, _ = setup_discography(args.input)

    if args.recheck:
        print("Re-checking UNCERTAIN/FAILED releases against MB...")
        with open(disc_path) as f:
            data = json.load(f)
        changed = 0
        for entry in data:
            if entry.get('mb_status') in ('UNCERTAIN', 'FAILED'):
                artist_name = entry.get('artist_name', '')
                if not artist_name:
                    continue
                status, mb_url = mb_check_release(entry['title'], artist_name)
                if status != entry['mb_status']:
                    mark = '✓' if status == 'EXISTS' else ('?' if status == 'UNCERTAIN' else '✗')
                    print(f"  {mark} [{entry['mb_status']} → {status}] {entry['title']}")
                    entry['mb_status'] = status
                    if mb_url:
                        entry['mb_url'] = mb_url
                    changed += 1
                time.sleep(1.1)
        if changed:
            save_disc(disc_path, data)
            print(f"  Updated {changed} entries in {disc_path}")
        else:
            print("  No status changes.")

    if args.setup_only:
        print("Setup complete (--setup-only). Not submitting.")
        return

    ensure_session()
    batch_process(disc_path, start_idx=args.start, limit=args.limit)


if __name__ == '__main__':
    main()
