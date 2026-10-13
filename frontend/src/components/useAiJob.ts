/** An AI step run as a job (see backend/app/ai_jobs.py): it keeps working and keeps its result
 *  if the page is refreshed or left. On load the page picks up the latest job of its kind:
 *  still running → it shows the step working and waits; done → it can show the result. */
import { useCallback, useEffect, useState } from 'react'
import { AiJob, AiJobKind, api } from '../api/client'
import { errorText } from './ui'
import { utcDate } from './Evidence'

export function useAiJob<T>(projectId: string, kind: AiJobKind, onDone?: (job: AiJob<T>) => void, storyId?: string) {
  const [job, setJob] = useState<AiJob<T> | null>(null)
  const [startError, setStartError] = useState<string | null>(null)
  const [startedHere, setStartedHere] = useState(false)

  useEffect(() => {
    let stopped = false
    setJob(null)
    api.latestAiJob(projectId, kind, storyId).then((j) => { if (!stopped) setJob(j as AiJob<T> | null) }).catch(() => {})
    return () => { stopped = true }
  }, [projectId, kind, storyId])

  const running = job?.status === 'running'
  useEffect(() => {
    if (!job || !running) return
    const id = window.setInterval(() => {
      api.aiJob(job.id).then((j) => {
        setJob(j as AiJob<T>)
        if (j.status === 'done') onDone?.(j as AiJob<T>)
      }).catch(() => {})
    }, 2000)
    return () => window.clearInterval(id)
  }, [job?.id, running])  // eslint-disable-line react-hooks/exhaustive-deps

  const start = useCallback(async (request: Record<string, unknown> = {}) => {
    setStartError(null)
    try { setJob(await api.startAiJob(projectId, kind, request) as AiJob<T>); setStartedHere(true) } catch (e) { setStartError(errorText(e)) }
  }, [projectId, kind])

  return {
    setJob: setJob as (job: AiJob<T> | null) => void,  // after a change to the job's result made elsewhere
    job, running, start,
    // A failure is news only if it was started here or ended lately, not an old one found on load.
    error: startError ?? (job?.status === 'failed' && (startedHere || recent(job.finished_at)) ? job.error : null),
    since: job ? elapsed(job.created_at) : '',
  }
}

/** "1 min 20 s" since the job started, for the busy label. */
export function elapsed(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - utcDate(iso).getTime()) / 1000))
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ''}`
}

const recent = (iso: string | null) => !!iso && Date.now() - utcDate(iso).getTime() < 15 * 60_000
