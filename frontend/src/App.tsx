import { BrowserRouter, Navigate, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import CrateMind from './pages/CrateMind'
import Crates from './pages/Crates'
import Duplicates from './pages/Duplicates'
import SmartCrates from './pages/SmartCrates'
import LibraryView from './components/library/LibraryView'
import ErrorBoundary from './components/ErrorBoundary'
import BpmReview from './pages/BpmReview'
import Export from './pages/Export'
import Jobs from './pages/Jobs'
import Reconciliation from './pages/Reconciliation'
import MetadataRepair from './pages/MetadataRepair'
import MetadataSanitation from './pages/MetadataSanitation'
import Quality from './pages/Quality'
import QualityReview from './pages/QualityReview'
import SetBuilder from './pages/SetBuilder'
import SsdSync from './pages/SsdSync'
import Settings from './pages/Settings'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<ErrorBoundary><LibraryView /></ErrorBoundary>} />
          <Route path="issues" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
          <Route path="enrichment" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
          <Route path="audit" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
          <Route path="folders" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
          <Route path="quality" element={<ErrorBoundary><Quality /></ErrorBoundary>} />
          <Route path="quality-review" element={<ErrorBoundary><QualityReview /></ErrorBoundary>} />
          <Route path="metadata-repair" element={<ErrorBoundary><MetadataRepair /></ErrorBoundary>} />
          <Route path="metadata-sanitation" element={<ErrorBoundary><MetadataSanitation /></ErrorBoundary>} />
          <Route path="bpm-review" element={<ErrorBoundary><BpmReview /></ErrorBoundary>} />
          <Route path="jobs" element={<ErrorBoundary><Jobs /></ErrorBoundary>} />
          <Route path="duplicates" element={<ErrorBoundary><Duplicates /></ErrorBoundary>} />
          <Route path="crates" element={<ErrorBoundary><Crates /></ErrorBoundary>} />
          <Route path="smart-crates" element={<ErrorBoundary><SmartCrates /></ErrorBoundary>} />
          <Route path="set-builder" element={<ErrorBoundary><SetBuilder /></ErrorBoundary>} />
          <Route path="exports" element={<ErrorBoundary><Export /></ErrorBoundary>} />
          <Route path="sync" element={<ErrorBoundary><SsdSync /></ErrorBoundary>} />
          <Route path="reconciliation" element={<ErrorBoundary><Reconciliation /></ErrorBoundary>} />
          <Route path="settings" element={<ErrorBoundary><Settings /></ErrorBoundary>} />

          <Route path="dashboard" element={<Navigate to="/" replace />} />
          <Route path="collection" element={<Navigate to="/" replace />} />
          <Route path="tracks" element={<Navigate to="/" replace />} />
          <Route path="export" element={<Navigate to="/exports" replace />} />
          <Route path="ssd-sync" element={<Navigate to="/sync" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
