// The app's admin page (Jira settings → Apps → Mirmera; Jira admins only): link this Jira
// site to a Mirmera workspace with a one-time code made in Mirmera, or unlink it.
import React, { useEffect, useState } from 'react';
import ForgeReconciler, {
  Button, Form, FormFooter, FormSection, Heading, Label, Link, SectionMessage, Spinner, Stack, Text, Textfield,
  useForm, useProductContext,
} from '@forge/react';
import { invoke } from '@forge/bridge';

function App() {
  const context = useProductContext();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const { handleSubmit, register, getFieldId } = useForm();

  const load = () => invoke('status').then((r) => {
    if (r.error) setError(r.error);
    else setStatus(r.data);
  });
  useEffect(() => { load(); }, []);

  async function link({ code }) {
    setBusy(true); setError(null);
    const r = await invoke('link', { code, siteUrl: context && context.siteUrl });
    setBusy(false);
    if (r.error) setError(r.error);
    else setStatus(r.data);  // linked: the form goes away
  }
  async function unlink() {
    setBusy(true); setError(null);
    const r = await invoke('unlink');
    setBusy(false);
    if (r.error) setError(r.error);
    else setStatus(r.data);
  }

  if (!status && !error) return <Spinner label="Asking Mirmera" />;
  return (
    <Stack space="space.200">
      <Heading size="medium">Mirmera</Heading>
      <Text>
        Mirmera shows, on each issue, the exploratory testing behind it: the risks, charters and sessions on a story,
        what hasn't been looked at yet, and what happened to the bugs it filed.
      </Text>
      {error && <SectionMessage appearance="error"><Text>{error}</Text></SectionMessage>}
      {status && status.linked && (
        <Stack space="space.100">
          <SectionMessage appearance="success">
            <Text>This Jira site is linked to the Mirmera workspace {status.workspace}.</Text>
          </SectionMessage>
          <Text>
            Everyone who can open an issue here sees its Mirmera panel: counts, titles and what's left, never the
            evidence, notes or keys.
          </Text>
          {status.app_url && <Link href={status.app_url} openNewTab>Open Mirmera</Link>}
          <Button appearance="danger" isDisabled={busy} onClick={unlink}>Unlink this site</Button>
        </Stack>
      )}
      {status && !status.linked && (
        <Form onSubmit={handleSubmit(link)}>
          <FormSection>
            <Text>
              In Mirmera, open Account, choose your workspace's Jira page and make a link code (workspace admins only).
              Paste it here within 30 minutes; it works once.
            </Text>
            <Label labelFor={getFieldId('code')}>Link code</Label>
            <Textfield {...register('code', { required: true })} placeholder="mjl_…" />
          </FormSection>
          <FormFooter>
            <Button appearance="primary" type="submit" isDisabled={busy}>{busy ? 'Linking…' : 'Link to Mirmera'}</Button>
          </FormFooter>
        </Form>
      )}
    </Stack>
  );
}

ForgeReconciler.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
