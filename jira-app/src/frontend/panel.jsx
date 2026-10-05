// The Mirmera panel on a Jira issue: the testing behind a story, or what happened to a bug
// Mirmera filed. Counts, titles and what's left; the evidence stays in Mirmera.
import React, { useEffect, useState } from 'react';
import ForgeReconciler, {
  Badge, Box, Heading, Inline, Link, List, ListItem, Lozenge, SectionMessage, Spinner, Stack, Strong, Text,
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

function Story({ story }) {
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

function Nothing({ panel }) {
  return (
    <Stack space="space.100">
      <Text>Mirmera has no testing on {panel.issue_key} yet.</Text>
      {panel.projects.length > 0 ? (
        panel.projects.map((p) => (
          <Text key={p.id}>
            {p.stories_url
              ? <Link href={p.stories_url} openNewTab>{`Import it as a story in ${p.name}`}</Link>
              : `Import it as a story in ${p.name} (Stories → Import).`}
          </Text>
        ))
      ) : (
        <Text>
          No project in {panel.workspace} files to this Jira project. Set its Jira project key in Mirmera (project
          Settings) to import issues from here.
        </Text>
      )}
    </Stack>
  );
}

function App() {
  const [result, setResult] = useState(null);
  useEffect(() => {
    invoke('issuePanel').then(setResult).catch(() => setResult({ error: "Mirmera's panel couldn't load." }));
  }, []);

  if (!result) return <Spinner label="Asking Mirmera" />;
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
            <Story story={s} />
          </Stack>
        ))}
        {panel.bugs.length > 0 && (
          <Stack space="space.100">
            <Heading size="xsmall">{panel.bugs.length === 1 ? 'The bug behind this issue' : 'Bugs filed to this issue'}</Heading>
            {panel.bugs.map((b) => <Bug key={b.id} bug={b} />)}
          </Stack>
        )}
        {panel.stories.length === 0 && panel.bugs.length === 0 && <Nothing panel={panel} />}
      </Stack>
    </Box>
  );
}

ForgeReconciler.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
