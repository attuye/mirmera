/** A part of a page that breaks shows what went wrong, instead of the whole page going blank. */
import React from 'react'

export default class ErrorBoundary extends React.Component<{ what: string; children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`Mirmera couldn't show ${this.props.what}:`, error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div role="alert" style={{ margin: '14px 0', padding: '10px 12px', borderRadius: 8, fontSize: 12.5, lineHeight: 1.5,
        border: '1px solid var(--danger)', background: 'var(--danger-bg)', color: 'var(--text)' }}>
        Mirmera couldn't show {this.props.what}: <span className="mono">{this.state.error.message}</span>.
        The rest of the page still works. Please send this message (and the browser console) so it can be fixed.
        <div><button onClick={() => this.setState({ error: null })} style={{ marginTop: 6, fontSize: 12 }}>Try again</button></div>
      </div>
    )
  }
}
