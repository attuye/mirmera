// The Mirmera panel on a Jira issue: the testing behind a story, or what happened to a bug
// Mirmera filed. Counts, titles and what's left; the evidence stays in Mirmera.
import React, { useEffect, useState } from 'react';
import ForgeReconciler, {
  Badge, Box, Button, Heading, Inline, Link, List, ListItem, Lozenge, SectionMessage, Spinner, Stack, Strong, Text,
} from '@forge/react';
import { invoke } from '@forge/bridge';

const RETEST = {
  running: ['Retest running', 'inprogress'],
  fixed: ['Retest: fixed', 'success'],
  still_fails: ['Retest: still fails', 'removed'],
  could_not_check: ["Retest couldn't check", 'moved'],
  no_test: ['No regression test to retest', 'default'],
};

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function Story({ story, onPlan }) {
  return (
    <Stack space="space.100">
      <Inline space="space.100" alignBlock="center">
        <Strong>{story.title}</Strong>
        <Lozenge>{story.project}</Lozenge>
        {story.sprint && <Lozenge appearance="new">{story.sprint}</Lozenge>}
      </Inline>
      <Text>
        {plural(story.risks, 'risk')} · {plural(story.charters, 'charter')} · {plural(story.sessions, 'session')}
        {story.minutes ? ` (${story.minutes} min)` : ''} · {plural(story.bugs, 'bug')}
        {story.high_bugs ? `, ${story.high_bugs} high` : ''}
      </Text>
      {story.left.length > 0 ? (
        <Stack space="space.050">
          <Text><Strong>Not looked at yet</Strong></Text>
          <List type="unordered">
            {story.left.slice(0, 8).map((line) => <ListItem key={line}>{line}</ListItem>)}
          </List>
          {story.left.length > 8 && <Text>And {story.left.length - 8} more in Mirmera.</Text>}
        </Stack>
      ) : (
        <Text>Nothing left that Mirmera knows of. Whether that's enough testing is the team's call.</Text>
      )}
      {story.risks === 0 && story.charters === 0 && (
        <Inline><Button onClick={onPlan}>Propose risks and charters</Button></Inline>
      )}
      {story.url && <Link href={story.url} openNewTab>Open the story in Mirmera</Link>}
    </Stack>
  );
}

function Bug({ bug }) {
  const [retest, look] = bug.retest ? RETEST[bug.retest] || [bug.retest, 'default'] : [null, null];
  return (
    <Stack space="space.050">
      <Inline space="space.100" alignBlock="center">
        <Strong>{bug.title}</Strong>
        {bug.severity && <Badge appearance={bug.severity === 'high' ? 'important' : 'default'}>{bug.severity}</Badge>}
        {retest && <Lozenge appearance={look}>{retest}</Lozenge>}
      </Inline>
      <Text>
        Found in {bug.project}{bug.found_in ? `, exploring "${bug.found_in}"` : ''}; filed here as {bug.filed_as}.
      </Text>
      {bug.url && <Link href={bug.url} openNewTab>See the evidence in Mirmera</Link>}
    </Stack>
  );
}

function Nothing({ panel, onPlan }) {
  return (
    <Stack space="space.100">
      <Text>Mirmera has no testing on {panel.issue_key} yet.</Text>
      {panel.projects.length > 0 ? (
        <Stack space="space.100">
          <Text>
            Mirmera can read this work item and propose risks and charters for it in {panel.projects[0].name}. A tester
            accepts, changes or drops them.
          </Text>
          <Inline>
            <Button appearance="primary" onClick={onPlan}>Plan testing with Mirmera</Button>
          </Inline>
        </Stack>
      ) : (
        <Text>
          No project in {panel.workspace} files to this Jira project. Set its Jira project key in Mirmera (project
          Settings) to import issues from here.
        </Text>
      )}
    </Stack>
  );
}

const POLL_MS = 3000;
const POLL_LIMIT = 60; // about 3 minutes

function App() {
  const [result, setResult] = useState(null);
  const [planning, setPlanning] = useState(null); // { message } while Mirmera plans
  const [planError, setPlanError] = useState(null);
  const load = () => invoke('issuePanel').then(setResult).catch(() => setResult({ error: "Mirmera's panel couldn't load." }));
  useEffect(() => { load(); }, []);

  async function plan() {
    setPlanError(null);
    setPlanning({ message: 'Asking Mirmera to plan…' });
    const started = await invoke('planTesting').catch(() => ({ error: "Mirmera couldn't be reached." }));
    if (started.error) { setPlanning(null); setPlanError(started.error); return; }
    let task = started.data;
    for (let i = 0; !task.done && i < POLL_LIMIT; i += 1) {
      setPlanning({ message: task.message || 'Mirmera is planning…' });
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      const next = await invoke('planStatus', { id: task.id }).catch(() => ({ error: 'lost' }));
      if (next.error) break;
      task = next.data;
    }
    setPlanning(null);
    if (!task.done) setPlanError('Mirmera is still planning. Reopen this work item in a minute to see the result.');
    else if (!task.ok) setPlanError(task.message);
    await load();
  }

  if (!result) return <Spinner label="Asking Mirmera" />;
  if (planning) {
    return (
      <Inline space="space.100" alignBlock="center">
        <Spinner size="small" label="Planning" />
        <Text>{planning.message}</Text>
      </Inline>
    );
  }
  if (result.error) {
    return (
      <SectionMessage appearance={result.status === 409 ? 'information' : 'warning'}>
        <Text>{result.error}</Text>
      </SectionMessage>
    );
  }
  const panel = result.data;
  return (
    <Box>
      <Stack space="space.200">
        {panel.stories.map((s) => (
          <Stack key={s.id} space="space.100">
            <Heading size="xsmall">Testing on this story</Heading>
            <Story story={s} onPlan={plan} />
          </Stack>
        ))}
        {panel.bugs.length > 0 && (
          <Stack space="space.100">
            <Heading size="xsmall">{panel.bugs.length === 1 ? 'The bug behind this issue' : 'Bugs filed to this issue'}</Heading>
            {panel.bugs.map((b) => <Bug key={b.id} bug={b} />)}
          </Stack>
        )}
        {planError && <SectionMessage appearance="warning"><Text>{planError}</Text></SectionMessage>}
        {panel.stories.length === 0 && panel.bugs.length === 0 && <Nothing panel={panel} onPlan={plan} />}
      </Stack>
    </Box>
  );
}

ForgeReconciler.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
