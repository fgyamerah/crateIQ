import type { ReactNode } from 'react'

interface Props {
  icon?: ReactNode
  title?: string
  message: ReactNode
  action?: ReactNode
}

/** Shared "nothing here" state for queues/tables/panels across pages. */
export default function EmptyState({ icon, title, message, action }: Props) {
  return (
    <div className="ui-empty-state">
      {icon && <span className="ui-empty-state-icon">{icon}</span>}
      {title && <strong className="ui-empty-state-title">{title}</strong>}
      <p className="ui-empty-state-message">{message}</p>
      {action && <div className="ui-empty-state-action">{action}</div>}
    </div>
  )
}
