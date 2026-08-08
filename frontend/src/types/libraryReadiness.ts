export type ReadinessSeverity = 'blocker' | 'warning' | 'optional'

export interface ReadinessReason {
  code: string
  severity: ReadinessSeverity
  message: string
  route: string | null
}

export interface LibraryReadiness {
  total_tracks: number
  ready: boolean
  blockers: ReadinessReason[]
  warnings: ReadinessReason[]
  optional: ReadinessReason[]
  coverage: Record<string, number>
  message: string
}
