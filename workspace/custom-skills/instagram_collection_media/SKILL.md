# Read Instagram Collection Outfits

## When this applies

- The user says he added items to the Instagram saved collection 「Museたん用」, or asks Muse to pick / wear an outfit from that collection, or a selection or shoot session needs collection outfits judged from the images themselves.
- Captions and metadata are NOT enough to judge an outfit (a caption once called a pleated culotte a balloon skirt, and media-understanding descriptions can be null). Judge from fetched images.

## Agreed scope

- This collection only. No constant patrol: refresh runs when the user says he added items, or inside a session he started.
- Fetched originals are never published, shared or sent anywhere. They are reference for Muse's own reading and for outfits she generates.

## Refresh after additions (the user's word is the trigger)

1. Update the collection metadata (upstream: Muse's built-in Instagram connector CLI, logged-in account required; ids are the operator's own):

```text
python3 ~/workspace/custom-connectors/custom.instagram_media/bin/fetch_collection.py --account-id <id> --collection-id <id> --cli /opt/hatch/bin/instagram-cli
```

2. Fetch representative images for the new posts (existing files are skipped):

```text
python3 ~/workspace/custom-connectors/custom.instagram_media/bin/instagram_media.py tops
```

3. Read the new representatives in batches of about 20 and update `~/workspace/instagram/muse-want-to-wear.md`.

## Picking an outfit

- First pass: read each candidate's representative `~/workspace/instagram/media/<shortcode>/1-small.jpg`. If the representative is not a full outfit shot (face close-up etc.), hold the post as undecided instead of rejecting it.
- Shortlist, then fetch all children (small) for just those posts:

```text
python3 ~/workspace/custom-connectors/custom.instagram_media/bin/instagram_media.py children <shortcode> [more...]
```

- Reels are judged from their poster image only; do not fetch video.
- If a chosen outfit's details (fabric, decoration) cannot be judged from the small, say so before escalating to the original-size file.

## Failures

- Exit 5 means Instagram answered 429 or 403: the run has already stopped. Report that plainly, do not retry in a loop, and leave the rest for a later run — `tops` resumes by skipping what is saved.
- A post whose manifest note reports a child-count mismatch gets its note relayed when that post matters.

## Available connectors

- [Instagram Collection Media (embed, no login)](../../custom-connectors/custom.instagram_media/CONNECTOR.md) for the helper's commands, output format and limits.
