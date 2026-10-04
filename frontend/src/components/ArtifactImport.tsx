/** Import an artifact instead of pasting it: a GitHub pull request, past bugs
 *  from GitHub or Jira, or a git diff from a repository on this computer. */
import { useState } from 'react'
import { api, Artifact, ArtifactImport as ImportBody, Project } from '../api/client'
import { ErrorLine, errorText, fieldLabel, input, primaryButton } from './ui'

const SOURCES: { key: ImportBody['source']; label: string; hint: string }[] = [
  { key: 'github_pr', label: 'GitHub pull request', hint: 'Its title, description and diff, as a Diff.' },
  { key: 'github_issues', label: 'GitHub issues (past bugs)', hint: 'Open and closed issues with a label, as Bug history.' },
  { key: 'jira_issues', label: 'Jira issues (past bugs)', hint: 'Issues matching a JQL query, as Bug history.' },
  { key: 'git_diff', label: 'Git diff on this computer', hint: 'What a branch adds, from a local repository, for work not pushed yet.' },
]

export default function ArtifactImport({ project, onImported }: { project: Project; onImported: (a: Artifact) => void }) {
  const [source, setSource] = useState<ImportBody['source']>('github_pr')
  const [f, setF] = useState({ ref: '', labels: 'bug', jql: '', repo_path: '', base: 'main', head: 'HEAD' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value })
  const hint = SOURCES.find((s) => s.key === source)!.hint
  const ready = source === 'github_pr' ? !!f.ref.trim() : source === 'git_diff' ? !!f.repo_path.trim() : true
  const defaultJql = project.jira_project_key ? `project = "${project.jira_project_key}" AND issuetype = Bug ORDER BY created DESC` : 'project = KEY AND issuetype = Bug'

  async function go() {
    setBusy(true); setError(null)
    try {
      const body: ImportBody = { source }
      if (source === 'github_pr') body.ref = f.ref.trim()
      if (source === 'github_issues') body.labels = f.labels
      if (source === 'jira_issues') body.jql = f.jql
      if (source === 'git_diff') Object.assign(body, { repo_path: f.repo_path, base: f.base, head: f.head })
      onImported(await api.importArtifact(project.id, body))
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  const field = (id: string, label: string, el: React.ReactNode, note?: string) => (
    <div style={{ flex: 1, minWidth: 120 }}>
      <label htmlFor={id} style={fieldLabel}>{label}</label>{el}
      {note && <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>{note}</div>}
    </div>
  )

  return (
    <div role="group" aria-label="Import" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {field('import-source', 'From', (
        <select id="import-source" value={source} onChange={(e) => { setSource(e.target.value as ImportBody['source']); setError(null) }} style={input}>
          {SOURCES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
      ), hint)}
      {source === 'github_pr' && field('import-ref', 'Pull request', <input id="import-ref" value={f.ref} onChange={set('ref')} style={input}
        placeholder={project.github_repo ? `42, or owner/repo#42, or its address` : 'owner/repo#42 or its address'} />,
        project.github_repo ? `A number alone means ${project.github_repo}.` : undefined)}
      {source === 'github_issues' && field('import-labels', 'Labels', <input id="import-labels" value={f.labels} onChange={set('labels')} style={input} placeholder="bug" />,
        project.github_repo ? `From ${project.github_repo}; comma-separated; empty for every issue.` : 'Set the GitHub repo in Settings first.')}
      {source === 'jira_issues' && field('import-jql', 'JQL', <input id="import-jql" value={f.jql} onChange={set('jql')} style={input} placeholder={defaultJql} />,
        'Empty: the project\'s bugs, newest first.')}
      {source === 'git_diff' && (
        <>
          {field('import-repo', 'Repository folder', <input id="import-repo" value={f.repo_path} onChange={set('repo_path')} style={input}
            placeholder="E:\code\kyc-app" />, 'On the computer running the Mirmera backend.')}
          <div style={{ display: 'flex', gap: 8 }}>
            {field('import-base', 'Base', <input id="import-base" value={f.base} onChange={set('base')} style={input} />)}
            {field('import-head', 'Branch', <input id="import-head" value={f.head} onChange={set('head')} style={input} />, 'What it adds since it left the base.')}
          </div>
        </>
      )}
      <ErrorLine message={error} />
      <div>
        <button onClick={go} disabled={!ready || busy} style={primaryButton(!ready || busy)}>{busy ? 'Importing…' : 'Import'}</button>
      </div>
    </div>
  )
}
