import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './styles/tokens.css'
import Projects from './pages/Projects'
import ProjectSettings from './pages/ProjectSettings'
import Dashboard from './pages/Dashboard'
import CharterSuggestions from './pages/CharterSuggestions'
import ActiveSession from './pages/ActiveSession'
import DebriefPage from './pages/Debrief'
import DebriefsList from './pages/Debriefs'
import Bugs from './pages/Bugs'
import Regression from './pages/Regression'
import ReportPage from './pages/Report'
import CoveragePage from './pages/Coverage'
import HeuristicsPage from './pages/Heuristics'
import RisksPage from './pages/Risks'
import { ProjectLayout, SessionRedirect, lastProjectId } from './project'

/** "/" opens the project used last in this browser, or the Projects page. */
function Home() {
  const last = lastProjectId()
  return <Navigate to={last ? `/projects/${last}/dashboard` : '/projects'} replace />
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/projects/:projectId" element={<ProjectLayout />}>
          <Route index element={<Navigate to="dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="risks" element={<RisksPage />} />
          <Route path="charters/suggest" element={<CharterSuggestions />} />
          <Route path="sessions/:sessionId" element={<ActiveSession />} />
          <Route path="sessions/:sessionId/debrief" element={<DebriefPage />} />
          <Route path="debriefs" element={<DebriefsList />} />
          <Route path="bugs" element={<Bugs />} />
          <Route path="regression" element={<Regression />} />
          <Route path="report" element={<ReportPage />} />
          <Route path="coverage" element={<CoveragePage />} />
          <Route path="heuristics" element={<HeuristicsPage />} />
          <Route path="settings" element={<ProjectSettings />} />
        </Route>
        {/* Links from before projects */}
        <Route path="/sessions/:sessionId" element={<SessionRedirect />} />
        <Route path="/sessions/:sessionId/debrief" element={<SessionRedirect debrief />} />
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
