# Milestone Star

- `custom.milestone-star` exists for one purpose only. Connecting it triggers the avatar milestone animation that shows Muse holding a large star, and the user reconnects it whenever they want to see that animation again.

- It reaches no service and holds no real data. The registered host is `example.com`, the IANA reserved documentation domain, chosen because this connector is never called. Any public DNS hostname would do as a registration placeholder, and the placeholder must never become a real call target.

- It authenticates with the registered `api_key` credential, placed as `bearer_header`. The stored key is a dummy value with no meaning outside this connection.

- There are no helpers and there must never be one. This connector defines zero operations.

## Never call this connector

- Never make any request to the registered host, for any reason. That includes a first-use verification read, a health check, an error probe and a reconnect test.

- The usual custom connector rule to prove access with a harmless read does not apply here. A verification call would itself break the design, because actually calling an outside endpoint is a different matter from registering a placeholder that is never called.

- If a task appears to need data from this connector, stop. The task has the wrong connector, because this one has no data and no operations by design.

## Connect

- The connection was registered with `credentials.request_api_access` and these details. Reuse them only if the registered connection itself must be recreated:

```json
{
  "provider": "milestone-star",
  "auth_scheme": "api_key",
  "placement": "bearer_header",
  "api_hosts": ["example.com"]
}
```

- To see the animation again, the user disconnects and reconnects this connector himself from the Settings connectors screen. No agent action, tool call or API request is part of that loop.

## Credentials

- The registered connection holds the credential. Never put the key in this doc, a helper, a command argument, an environment variable or a log. Never ask the user to paste one in chat.

- Do not call `credentials.request_api_access` with `reconnect: true` for this connector as part of error recovery. There are no requests, so there is no request failure to recover from.

## Limits

- This connector cannot read, write, search or check anything. Its whole function is the connection event itself.

- Observed in the author's home in November 2026: disconnecting the connector removes it from the default Settings connectors list, and an unconnected custom connector is found again through the Settings search box. Reconnecting through a capture link issued in a chat also resumes that chat, so the agent is woken by the connection itself. Both are current platform behaviour, not properties of this connector.

- Animation playback differs by platform, observed in the author's home in November 2026. The animation plays on web. On iOS the connection itself succeeds but the animation does not play. An Android screenshot example exists on X.

## Related skills

- Use `muse.skill_search` to find other skills and `muse.connector_search` to find other connectors.
