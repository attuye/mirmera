import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './styles/tokens.css'
import Dashboard from './pages/Dashboard'
import CharterSuggestions from './pages/CharterSuggestions'
import ActiveSession from './pages/ActiveSession'
import DebriefPage from './pages/Debrief'
import DebriefsList from './pages/Debriefs'
import Bugs from './pages/Bugs'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/charters/suggest" element={<CharterSuggestions />} />
        <Route path="/sessions/:sessionId" element={<ActiveSession />} />
        <Route path="/sessions/:sessionId/debrief" element={<DebriefPage />} />
        <Route path="/debriefs" element={<DebriefsList />} />
        <Route path="/bugs" element={<Bugs />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
