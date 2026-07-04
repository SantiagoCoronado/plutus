import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import BacktestDetail from './pages/BacktestDetail.tsx'
import Backtests from './pages/Backtests.tsx'
import Dashboard from './pages/Dashboard.tsx'
import Inbox from './pages/Inbox.tsx'
import Mandates from './pages/Mandates.tsx'
import Research from './pages/Research.tsx'
import Screener from './pages/Screener.tsx'
import Watchlists from './pages/Watchlists.tsx'

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'inbox', element: <Inbox /> },
      { path: 'asset/:id', element: <Research /> },
      { path: 'watchlists', element: <Watchlists /> },
      { path: 'screener', element: <Screener /> },
      { path: 'backtests', element: <Backtests /> },
      { path: 'backtests/:id', element: <BacktestDetail /> },
      { path: 'mandates', element: <Mandates /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
