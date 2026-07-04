/** Shared e2e paths (no side effects — safe to import from specs/config). */
import path from 'path'
import { fileURLToPath } from 'url'

const here = path.dirname(fileURLToPath(import.meta.url))
export const HOME = path.join(here, '..', '.e2e-home')
export const DEMO = path.join(HOME, 'demo-repo')
export const PY = path.join(here, '..', '..', 'backend', '.venv', 'Scripts', 'python.exe')
