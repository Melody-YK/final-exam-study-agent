import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './app/App'
import './styles/base.css'
import './styles/auth-admin.css'
import './styles/workspace.css'
import './styles/responsive.css'

const root = document.getElementById('root')

if (root === null) {
  throw new Error('Missing #root application mount point')
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
