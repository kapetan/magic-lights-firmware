import * as React from 'react'
import { createRoot } from 'react-dom/client'
import Application from './app.jsx'

const el = document.getElementById('app')
const root = createRoot(el)

root.render(<Application />)
