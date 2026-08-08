import { apiFetch } from './client'
import type { LibraryReadiness } from '../types/libraryReadiness'

export const fetchLibraryReadiness = () => apiFetch.get<LibraryReadiness>('/library/readiness')
