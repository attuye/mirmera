// The app's backend: runs on Atlassian's servers, calls Mirmera with invokeRemote().
// Atlassian adds a signed Forge invocation token to each call; Mirmera checks it and
// knows which Jira site is asking. No Mirmera secret is kept in the app.
//
// Two resolvers, one per module, so the issue panel can't call what only the admin page
// should (linking and unlinking the site). The issue key comes from the context Jira gives
// the resolver, not from the panel's payload: the panel can only ask about its own issue.
import Resolver from '@forge/resolver';
import { invokeRemote } from '@forge/api';

async function mirmera(path, method = 'GET', body) {
  let res;
  try {
    res = await invokeRemote('mirmera', {
      path,
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    return { error: "Mirmera can't be reached. Check that it is running and the app's remote address is right." };
  }
  let data = null;
  try {
    data = await res.json();
  } catch (err) {
    data = null;
  }
  if (!res.ok) {
    const detail = data && typeof data.detail === 'string' ? data.detail : `Mirmera answered ${res.status}.`;
    return { error: detail, status: res.status };
  }
  return { data };
}

const panel = new Resolver();
panel.define('issuePanel', async ({ context }) => {
  const issue = context.extension && context.extension.issue;
  const project = context.extension && context.extension.project;
  if (!issue || !issue.key) return { error: 'Open an issue to see what Mirmera knows about it.' };
  return mirmera('/jira-app/issue-panel', 'POST', { issue_key: issue.key, project_key: project ? project.key : null });
});

// The admin page is shown to Jira admins only.
const admin = new Resolver();
admin.define('status', async () => mirmera('/jira-app/status'));

admin.define('link', async ({ payload, context }) => {
  const code = String((payload && payload.code) || '').trim();
  if (!code) return { error: 'Paste the code from Mirmera.' };
  return mirmera('/jira-app/link', 'POST', { code, site_url: context.siteUrl || (payload && payload.siteUrl) || null });
});

admin.define('unlink', async () => mirmera('/jira-app/link', 'DELETE'));

export const panelHandler = panel.getDefinitions();
export const adminHandler = admin.getDefinitions();
