# CrateIQ Design Direction

Status: proposed foundation for future implementation; this document does not redesign active pages.

## Visual direction

CrateIQ should feel like a calm operations console for a valuable, fragile library: dark, focused, legible and evidence-led. The visual hierarchy should make scope, safety, status and next action obvious before metadata detail. Density is useful for professional library work, but density must be structured with progressive disclosure rather than small text and weak contrast.

Design principles:

1. Evidence before action.
2. Review, apply and verify are visibly different.
3. Calm surfaces for routine work; strong emphasis only for blockers and high-risk actions.
4. Technical detail is available without being the first thing every user must parse.
5. Unknown is a valid state; never style missing data as success.
6. Every operation explains scope, effect, destination and next action.
7. Keyboard and screen-reader behavior are part of the component contract.

## Proposed tokens

Tokens are a direction, not a mandate to replace the existing stylesheet in Phase 0.

```css
:root {
  --color-bg: #0b0f14;
  --color-surface: #111821;
  --color-surface-raised: #17212c;
  --color-border: #263442;
  --color-text: #edf3f8;
  --color-text-secondary: #b7c4cf;
  --color-text-muted: #8c9aa6;
  --color-accent: #68a7ff;
  --color-accent-strong: #3d87ed;
  --color-success: #45c486;
  --color-warning: #e8b04d;
  --color-danger: #ef7373;
  --color-info: #73c4dd;
  --focus-ring: #9ac7ff;
  --radius-sm: 4px;
  --radius-md: 8px;
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
}
```

Contrast targets: normal text at least 4.5:1, large text at least 3:1, UI boundaries/focus indicators at least 3:1. Muted text must remain readable and must not be the only carrier of actionable meaning.

## Typography

- Use a highly legible sans-serif for interface text; existing IBM Plex Sans is a reasonable starting point.
- Use a monospace face only for paths, IDs, commands and logs; existing JetBrains Mono is suitable.
- Base text should remain at least 14px for dense data and 16px for primary reading surfaces.
- Use 1.4–1.6 line height for body/help text; avoid compressed paragraphs.
- Headings should describe the user task, not implementation modules.
- Numeric metrics should use tabular numerals where available and include labels/context.

## Density and layout

- Desktop: persistent navigation plus a wide content workspace.
- Tablet: collapsible navigation, fewer visible columns, inspector as drawer.
- Mobile: navigation drawer, track cards instead of wide tables, stacked filters, bottom-safe dialogs and explicit action bars.
- Use a consistent page header: title, one-sentence purpose, scope/readiness, and safe primary action.
- Prefer 8px spacing rhythm with larger breaks between task groups.
- Keep high-risk actions separated from routine navigation and use sentence-case labels that state the effect.

## Tables

- Tables are for comparison; cards are for mobile and focused review.
- Required table features: column controls, stable sort indicator, selection semantics, keyboard navigation, pagination/virtualization status, row focus and a clear empty state.
- Do not put every technical field in the default view. Default columns should be artist, title, issue/status, confidence/readiness and one useful operational signal.
- Long paths should truncate visually but remain available to copy and to assistive technology.
- Row actions must not hide whether they review, preview, apply or open detail.
- Use a summary row/card for mobile rather than horizontal scrolling as the only path.

## Forms and filters

- Labels remain visible; placeholders are not labels.
- URL-backed filters should have a clear “what is filtered” summary and a reset control.
- Search should show debounce/loading state without moving focus.
- Invalid, blocked and unavailable fields need text explanations and recovery guidance.
- Do not enable a high-risk submit button merely because a form is syntactically complete; readiness and safety eligibility are separate.

## Status colors and language

Color is supplementary. Every status uses text, icon/shape and accessible semantics.

| Status | Color direction | Required wording |
|---|---|---|
| Ready/verified | green | “Verified” or “Ready” plus evidence/time |
| Review/attention | amber | “Needs review” plus reason |
| Blocked | red | “Blocked” plus blocker and next action |
| Failed | red | “Failed” plus operation/error and retry guidance |
| Running | blue | “Running” plus real progress or “progress unavailable” |
| Deferred/unknown | gray/blue | Explicit “Deferred” or “Unknown”; never green |
| Preview/dry run | neutral/blue outline | “Preview only — no library changes” |

Never use red/green alone to communicate a decision. Do not show a percentage unless its denominator and source are real.

## Buttons and actions

- Primary button: one safe next action per page.
- Secondary button: inspect, refresh, filter or open detail.
- Destructive/high-risk button: explicit effect, warning, scope and confirmation; do not use euphemisms like “Go” or “Clean up”.
- Preview button: always visually distinct and adjacent to apply, with “Preview only” language.
- Apply button: disabled until eligibility and confirmation requirements are satisfied.
- Cancel: available for dialogs and supported jobs; explain best-effort cancellation.
- Minimum target: 44×44 CSS px for touch controls.

## Dialogs

Every dialog must have a visible title, clear scope, effect summary, warning/blocker section, cancel action, explicit confirm label and focus management. High-risk dialogs should show current → proposed values and destination paths. Escape closes only when safe; unsaved review decisions must not disappear silently.

## Responsive strategy

- Desktop tables may expose more columns; tablet hides lower-priority columns; mobile uses cards.
- Track inspector becomes a drawer with a persistent selected-track heading.
- Queue bulk actions become a bottom action bar or stacked action group.
- Publish steps remain sequential but allow returning to prior read-only steps without losing the preview artifact.
- Dialog content must fit narrow viewports without clipped confirmation text.
- Avoid hover-only affordances.

## Accessibility requirements

- WCAG 2.2 AA contrast and keyboard support are release gates.
- Use landmarks, heading hierarchy, semantic tables/lists and correctly associated labels.
- Implement focus-visible styles, focus retention in tables, focus trap/return in dialogs and Escape behavior.
- Expose row selection and sort state to assistive technology.
- Announce job completion/failure and review/apply results through polite live regions.
- Respect `prefers-reduced-motion` and never encode progress only through animation.
- Provide text equivalents for icons and non-color status markers.
- Test with keyboard-only navigation and at least one screen reader smoke path.

## Prohibited visual patterns

- No fake waveform, energy, compatibility or “AI certainty” visuals without real data.
- No green success state for unknown, stale or preview-only results.
- No unlabeled icon-only high-risk controls.
- No hidden destructive action behind a generic overflow menu.
- No technical error swallowed into an empty state.
- No tiny muted text for safety-critical warnings.
- No progress bars with invented percentages.
- No modal chains that make scope or destination unclear.
- No marketing-style dashboard metrics that lack freshness, denominator or source.
