# mb-batch-submit

A Playwright-based tool for bulk-submitting an artist's discography from Deezer to MusicBrainz. Drives the MusicBrainz Knockout.js release editor programmatically — no manual clicking required after the initial login.

Built for personal archival use. Respects MusicBrainz's rate limits (12 second delay between submissions).

## What it does

1. Fetches the full discography for a Deezer artist (albums, EPs, singles)
2. Checks each release against the MusicBrainz API to find what's already there
3. For missing releases: creates the release group, sets artist credits, parses tracks, and submits
4. Saves progress to a JSON file so interrupted runs can be resumed

## Requirements

- Python 3.9+
- A MusicBrainz account
- Deezer artist URL or ID

```bash
pip install -r requirements.txt
playwright install chromium
```

## Setup

Set your contact email in the environment (required by MusicBrainz's bot policy):

```bash
export MB_CONTACT_EMAIL="you@example.com"
```

On first run the script opens a headed browser window for you to log in to MusicBrainz. Your session is saved to `~/.config/musicbrainz/browser_state.json` and reused on subsequent runs.

## Usage

```bash
# Fetch discography + check MB, then submit all missing releases
python3 mb_batch_submit.py https://www.deezer.com/artist/123

# Inspect only — fetch and check MB but do not submit
python3 mb_batch_submit.py https://www.deezer.com/artist/123 --setup-only

# Resume from a saved JSON (skips the Deezer/MB setup phase)
python3 mb_batch_submit.py /tmp/artist_discography.json

# Resume from a specific index
python3 mb_batch_submit.py /tmp/artist_discography.json --start 10

# Submit at most N releases
python3 mb_batch_submit.py /tmp/artist_discography.json --limit 5

# Re-verify UNCERTAIN/FAILED entries against MB API before submitting
python3 mb_batch_submit.py /tmp/artist_discography.json --recheck

# Pre-seed the MBID cache for ambiguous artist names
python3 mb_batch_submit.py https://www.deezer.com/artist/123 --mbid "Artist Name=<mbid>"
```

## Discography JSON

The setup phase saves a file to `/tmp/<artist-slug>_discography.json`. Each entry tracks:

- `mb_status`: `EXISTS` / `MISSING` / `UNCERTAIN` / `FAILED`
- `mb_url`: MusicBrainz URL (set after submission or if found during setup)
- `rg_mbid`: Release group MBID (cached so a failed release can retry without creating a duplicate RG)

The file is updated after every submission, so it's safe to kill the script and resume.

## MusicBrainz bot policy

This tool is for personal archival use. If you use it, you are responsible for the edits it makes under your account. Please:

- Keep `DELAY_BETWEEN_RELEASES` at 12 seconds or higher
- Set `MB_CONTACT_EMAIL` to a real address so MusicBrainz staff can reach you
- Review the [MusicBrainz bot policy](https://musicbrainz.org/doc/Bot_Policy) before running large batches
- Don't use it to submit inaccurate data

## Notes

- Designed for **beginner MusicBrainz accounts** — handles all the confirmation checkboxes that block submission for new accounts (`confirm-feat`, `confirm-eti`, `confirm-miscapitalized-titles`, etc.)
- Single-artist and multi-artist (featured) credits are both supported
- If an artist doesn't exist on MusicBrainz yet, the script creates them automatically
- Session cookies expire after a few hours; the script detects expiry and aborts cleanly so you can re-login and resume

## License

MIT
