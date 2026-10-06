# Mirmera for Jira

An [Atlassian Forge](https://developer.atlassian.com/platform/forge/) app that puts a
**Mirmera** panel on every Jira issue. For a story, the panel shows the risks, charters and
sessions behind it and what hasn't been looked at yet. For a bug Mirmera filed, it shows
where the bug was found and what the latest retest said. When Mirmera knows nothing about
the issue, the panel links to the project's Stories page so you can import it.

Jira Cloud only. Atlassian's older Connect framework is losing support, so this app is
built on Forge.

## How it fits together

```
Jira issue ──▶ panel (UI Kit) ──invoke──▶ resolver (Forge function, on Atlassian)
                                              │ invokeRemote('mirmera', …)
                                              ▼   + Forge invocation token, signed by Atlassian
                                   Mirmera API  /jira-app/*
```

- The app keeps no Mirmera secret. Atlassian signs every call with a **Forge invocation
  token** (a JWT). Mirmera checks the signature against Atlassian's published keys. It also
  checks that the token was issued for this app (`JIRA_APP_ID`) and hasn't expired.
- A Jira site sees nothing until it is **linked** to a Mirmera workspace. A workspace admin
  makes a one-time code in Mirmera. A Jira admin pastes it into the app's admin page.
- The resolver takes the issue key from the context Jira gives it, not from the browser, so
  a panel can only ask about its own issue. Linking and unlinking go through the admin
  page's own resolver, and Jira shows that page only to Jira admins.
- Everyone who can open an issue sees its panel. So the panel shows counts, titles and
  what's left, and never evidence, notes or keys.

## Set it up

You need:

- an Atlassian developer account and a Jira Cloud site where you're an admin (a free
  [developer site](https://go.atlassian.com/cloud-dev) works);
- Node.js 22;
- Mirmera's API reachable from the internet over HTTPS. While developing, use a tunnel such
  as `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`.

```powershell
npm install -g @forge/cli
forge login                      # an API token from id.atlassian.com
cd jira-app
npm install
forge register                   # writes this app's ID into manifest.yml (app.id)
```

1. In `manifest.yml`, set `remotes[0].baseUrl` to your Mirmera API's public address, e.g.
   the tunnel's `https://….trycloudflare.com`. Use the API, not the frontend.
2. In Mirmera's `backend/.env`, set `JIRA_APP_ID` to the ID that `forge register` wrote
   (`ari:cloud:ecosystem::app/…`). Set `PUBLIC_APP_URL` too, so the panel can link to
   Mirmera's pages. Restart the backend.
3. Deploy and install:

   ```powershell
   forge deploy
   forge install --product jira --site your-site.atlassian.net
   ```

4. In Mirmera: **Account → your workspace → Jira → Make a link code**.
5. In Jira: **Settings → Apps → Mirmera**, paste the code, then **Link to Mirmera**.
6. Open any issue. The **Mirmera** panel is under the issue's action buttons (the app icon).

`forge tunnel` runs the resolver on your machine while you change it. `forge logs` shows
what the resolver printed.

Changing `baseUrl` or the modules needs another `forge deploy`. Atlassian may then ask
Jira admins to approve the new version.

## Check it without an Atlassian account

```powershell
npm run check
```

This validates `manifest.yml` against Forge's own schema and bundles the resolver and both
screens. CI runs the same check. `forge lint` and `forge deploy` check more, but they need
`forge login`.

## Files

| File | What it is |
| --- | --- |
| `manifest.yml` | Modules (issue panel, admin page, Rovo agent), the two resolver functions, the `mirmera` remote |
| `src/index.js` | Resolvers: `issuePanel` for the panel; `status`, `link`, `unlink` for the admin page |
| `src/frontend/panel.jsx` | The issue panel (UI Kit) |
| `src/frontend/admin.jsx` | The admin page: link with a code, or unlink |
| `scripts/check.mjs` | `npm run check` |

## Plan testing from the panel (no Rovo needed)

When Mirmera has no testing on a work item, the panel shows **Plan testing with Mirmera**.
The app reads the work item's summary and description, and Mirmera plans exactly as if the
item were assigned to the agent:

- the work item becomes a story;
- the AI proposes risks for it, and charters for those risks;
- when the Mirmera project has an app URL, the exploration agent then explores the first
  charter in the app and drafts the bugs it finds, for a tester to review;
- the panel shows progress while it works, then the result. Exploring can take a few minutes.

For a story that's already in Mirmera with no risks or charters yet, the button reads
**Propose risks and charters**.

## The Mirmera agent (Rovo)

The app also adds **Mirmera** as a Rovo agent (`rovo:agentConnector`). It needs Rovo on the
Jira site.

- **Assign a work item to Mirmera.** It becomes a story in the Mirmera project whose Jira
  project key matches. The AI proposes risks for the story and charters for those risks.
  All of them stay **proposed** until a tester accepts them. Assigning it again doesn't
  duplicate anything: Mirmera says it's already planned.
- **Mention @Mirmera in a comment.** Mirmera answers with what's left to test. If the comment
  asks it to plan (it contains "plan", "risks" or "charters"), it plans as if assigned.
- **Follow-up questions in the Rovo chat** are answered about the same work item.

Jira shows Mirmera's answer in the work item. Agents can't post comments themselves, so you
choose **Draft comment** to turn the answer into one.

How it works: Jira calls Mirmera's `/jira-app/a2a` directly (Agent2Agent 1.0, JSON-RPC
`SendMessage` / `GetTask` / `CancelTask`), with the same Atlassian-signed token as the panel.
Planning runs in the background, and Jira polls until it's done. Every request appears on
Mirmera's Jira page, under **Mirmera agent**.

After updating from a version without the agent, run `forge deploy`, then
`forge install --upgrade`. The new `read:jira-work` permission needs a Jira admin's
approval.

## What's next

- A bug assigned to Mirmera → try to reproduce it.
- A fixed bug → retest it.
