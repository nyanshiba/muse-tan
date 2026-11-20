# Build Spaces with Live Data

Choose how a Space gets its data before building: bake it in (static) or read it live (fullstack).

- Use a static Space when the content is fixed at build time. Use a fullstack Space when the page must show data that changes outside the artifact — a ledger file, a JSON export, any workspace file another process keeps updating.

- Fullstack server actions run on this VM and can read files under `/home/hatch/workspace/` directly by absolute path. A read at view time does not go through the artifact's database.

- Follow the live-read pattern (working example: the Muse Dashboard v2 Space): one server action with no arguments reads the source file at request time and returns its contents as-is; the client fetches on open and offers a reload button. This is pull-type — add no refresh cron for it.

- Never bake changing data into the page, and never copy it into the artifact's database. Either choice reintroduces a regeneration or sync step; with live read, updating the source file is the whole update.

- Surface read or parse failures as-is. Never fill a failed read with stale or dummy data.

- A change of kind (static ↔ fullstack) is a new create, not an edit, and saved data does not carry over. The new artifact gets a new slug (e.g. a `-2` suffix); the old artifact remains until it is deleted separately.

- Verify a live-read Space by invoking its action directly (`artifact.invoke_action`) and comparing the returned count and content against the source file.
