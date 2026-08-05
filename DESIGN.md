---
name: CrateIQ
description: Dense dark DJ operations dashboard for safe, review-first library work.
colors:
  deck-black: "#080d14"
  deep-black: "#050910"
  panel-navy: "#111924"
  elevated-navy: "#17212e"
  cyan-signal: "#20d4d8"
  teal-action: "#14b8a6"
  violet-harmonic: "#a78bfa"
  coral-warning: "#fb7185"
  strong-text: "#eef6fb"
  muted-text: "#91a2b6"
  divider: "#243244"
typography:
  title:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "21px"
    fontWeight: 680
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.06em"
rounded:
  sm: "6px"
  md: "10px"
  lg: "14px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "20px"
  xl: "28px"
components:
  button-primary:
    backgroundColor: "{colors.teal-action}"
    textColor: "{colors.deep-black}"
    rounded: "{rounded.sm}"
    padding: "7px 14px"
  card:
    backgroundColor: "{colors.panel-navy}"
    textColor: "{colors.strong-text}"
    rounded: "{rounded.lg}"
    padding: "20px 24px"
  input:
    backgroundColor: "{colors.deck-black}"
    textColor: "{colors.strong-text}"
    rounded: "{rounded.sm}"
    padding: "7px 10px"
---

# Design System: CrateIQ

## Overview

**Creative North Star: "The Night Deck"**

CrateIQ should feel like a precise DJ control surface used in a dim room: dense, focused, and alive with only the color needed to find state and action quickly. The existing Library mockup and implemented Library workspace are the visual authority. Operational clarity and trustworthy safety messaging outrank decoration.

**Key Characteristics:**

- Full-width, compact workspaces with persistent navigation and strong table density.
- Near-black navy layers separated by tonal contrast and restrained borders.
- Cyan and teal for action and selection, violet for harmonic context, coral for risk.
- Music-player details that never imply audio analysis or writes that do not exist.

## Colors

The palette uses dark navy fields with luminous but controlled accents. Cyan Signal marks active selection and focus; Teal Action carries safe primary actions; Violet Harmonic identifies musical-key context; Coral Warning is reserved for degraded or risky states.

**The Signal Color Rule.** Accent color communicates selection, action, musical context, or status; it is not ambient decoration.

## Typography

**Display Font:** Inter with the system sans-serif fallback.
**Body Font:** Inter with the system sans-serif fallback.
**Label/Mono Font:** JetBrains Mono is limited to BPM, key, paths, timestamps, and other measured data.

**Character:** Compact and neutral, with strong numerical scanability. Titles use weight and spacing rather than oversized scale.

**The Measurement Rule.** Monospace belongs to values and code-like data, never to ordinary interface copy.

## Layout

The app uses a fixed collapsible sidebar and a fluid full-width workspace. Primary operational views pair a flexible dense table or queue with a 340–380px inspector rail. Spacing follows a compact 4/8/12/20/28px rhythm. Below tablet widths, multi-column workspaces stack without hiding functionality.

## Elevation & Depth

Depth comes from navy tonal layers, a single subtle border, and soft downward shadows. Active controls may add a restrained cyan focus glow. Do not stack a strong border and a strong shadow on the same surface.

**The Deck Layer Rule.** Every elevation must clarify workspace hierarchy: shell, panel, selected row, or active control.

## Shapes

Controls use compact 6px corners, table/card internals use 10px corners, and primary panels use 14px corners. Pills are limited to tags, counts, and filter chips. Selected table rows remain rectangular and flush with their table.

## Components

### Buttons

Primary buttons use a teal-to-cyan action field with dark text. Ghost buttons sit on a dark translucent field and gain a cyan-tinted border on hover or focus. Disabled state lowers contrast without removing the label.

### Chips

Chips are compact, bordered, and reserved for filters, tags, Camelot keys, ratings, and status. Selected chips use a tinted accent field plus readable text.

### Cards / Containers

Panels use a deep navy gradient, a 1px divider border, 14px corners, and a soft downward shadow. Avoid nested card stacks; use sections and dividers inside an inspector.

### Inputs / Fields

Fields use the deck background, subtle border, 6px corners, strong text, and a visible cyan focus ring. Placeholders use muted text with readable contrast.

### Navigation

The sidebar is the darkest persistent layer. Active destinations receive a narrow cyan edge and teal-cyan wash; icons and labels brighten together.

### DJ Data Table

Tables are compact, full-width, and sticky-headed. Hover is a subtle cyan wash; selection is stronger and remains legible without relying on color alone. BPM, key, Camelot, rating, and review state should scan in one pass.

## Do's and Don'ts

### Do:

- **Do** preserve dense operational layouts and full-width workspaces.
- **Do** use the Library mockup at `docs/mockups/library.webp` as the visual reference.
- **Do** keep safety and degraded states explicit in text as well as color.
- **Do** preserve the existing accessible Camelot wheel and treat it as a signature component.

### Don't:

- **Don't** imply that a decorative waveform is analyzed audio data.
- **Don't** obscure write boundaries behind friendly labels or visual polish.
- **Don't** introduce light page canvases, generic marketing cards, or decorative glass effects.
- **Don't** overwrite or recolor trusted BPM, key, cue, or Mixed In Key data.
