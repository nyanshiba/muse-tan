# Fetch Instagram Collection Images

## When this applies

- The user says he added items to the designated Instagram saved collection, or asks Muse to fetch or read images from that collection.
- Captions and metadata are NOT the images themselves: a caption can misdescribe what a post actually shows, and media-understanding descriptions can be null. When the content matters, judge from the fetched images, not from text about them.

## Agreed scope

- The designated collection only, via its metadata file `~/workspace/instagram/muse-collection.json`. No constant patrol: refresh runs when the user says he added items, or inside a session he started.
- Fetched originals are never published, shared or sent anywhere. They are reference for Muse's own reading.

## Refresh after additions (the user's word is the trigger)

1. Update the collection metadata (upstream: Muse's built-in Instagram connector CLI, logged-in account required; ids are the operator's own):

```text
python3 ~/workspace/custom-connectors/custom.instagram_media/bin/fetch_collection.py --account-id <id> --collection-id <id> --cli /opt/hatch/bin/instagram-cli
```

2. Fetch representative images for the new posts (existing files are skipped):

```text
python3 ~/workspace/custom-connectors/custom.instagram_media/bin/instagram_media.py tops
```

3. Read the new representatives in batches of about 20.

## Reading more of a post

- The representative is child 1 (`~/workspace/instagram/media/<shortcode>/1-small.jpg`). If it does not show the post's main content (a close-up, a crop), hold the post as undecided instead of judging it from the representative alone.
- Fetch all children (small) for specific posts when their full content matters:

```text
python3 ~/workspace/custom-connectors/custom.instagram_media/bin/instagram_media.py children <shortcode> [more...]
```

- Reels are judged from their poster image only; do not fetch video.
- If a post's details cannot be judged from the small image, say so before escalating to the original-size file.

## Failures

- Exit 5 means Instagram answered 429 or 403: the run has already stopped. Report that plainly, do not retry in a loop, and leave the rest for a later run — `tops` resumes by skipping what is saved.
- A post whose manifest note reports a child-count mismatch gets its note relayed when that post matters.

## Available connectors

- [Instagram Collection Media (embed, no login)](../../custom-connectors/custom.instagram_media/CONNECTOR.md) for the helper's commands, output format and limits.
