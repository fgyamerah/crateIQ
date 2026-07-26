import type { ReactNode } from 'react'

export type StatusStripTone = 'info' | 'good' | 'warn' | 'danger'

interface Props {
  tone: StatusStripTone
  icon?: ReactNode
  /** Main message content, shown on the first line. */
  children: ReactNode
  /** Optional bullet list of sub-messages (e.g. individual failing checks). */
  details?: string[]
  /** Optional small trailing note, e.g. "Local diagnostic only". */
  footnote?: ReactNode
  /** Optional right-aligned actions (e.g. a "Recheck" or "Review" button). */
  actions?: ReactNode
  onDismiss?: () => void
  role?: 'status' | 'alert'
  className?: string
}

/**
 * Shared compact status/warning strip: full 1px border + tinted background,
 * never a colored side border. Used for runtime/degraded/warning messaging
 * across the app (Library's own runtime strip renders the equivalent
 * .lib-runtime-strip/.lib-status-strip markup directly since it predates
 * this primitive; new call sites should use StatusStrip instead).
 */
export default function StatusStrip({
  tone,
  icon,
  children,
  details,
  footnote,
  actions,
  onDismiss,
  role = 'status',
  className,
}: Props) {
  return (
    <div
      className={`ui-status-strip ui-status-strip--${tone}${className ? ` ${className}` : ''}`}
      role={role}
    >
      {icon && <span className="ui-status-strip-icon">{icon}</span>}
      <div className="ui-status-strip-body">
        <div className="ui-status-strip-message">{children}</div>
        {details && details.length > 0 && (
          <ul className="ui-status-strip-list">
            {details.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        )}
        {footnote && <div className="ui-status-strip-footnote">{footnote}</div>}
      </div>
      {actions && <div className="ui-status-strip-actions">{actions}</div>}
      {onDismiss && (
        <button
          className="ui-status-strip-dismiss"
          onClick={onDismiss}
          aria-label="Dismiss"
          type="button"
        >
          ×
        </button>
      )}
    </div>
  )
}
