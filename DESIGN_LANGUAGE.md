# RollMetrics Design Language

RollMetrics is an arena timing and film-telemetry instrument for Brazilian Jiu-Jitsu. Every surface, glyph, and typographic choice prioritizes mat-side clarity, instant IBJJF score legibility, and zero decorative or developer clutter.

## 1. Design Philosophy: Tatami Geometry & Swiss Timing

- **Single-Focus Stage**: Match footage, the synchronized IBJJF scoreboard strip, and timestamped Caio Terra Association (CTA) cues live on one calm, breathing canvas. Engineering internals (index dimensions, database counts, codec labels) never appear in the interface.
- **Pure Wordmark**: Always render the brand name as `RollMetrics` without domain suffixes.
- **Instrument Contrast**: Matte obsidian tatami surfaces recede behind live match footage while tabular numerals and stitched Jiu-Jitsu belt indicators provide immediate orientation.

## 2. Brand Mark: `03 / Kamon Lock`

- **Construction**: A `45`-degree rotated square (diamond) outer frame with sharp `90`-degree mitered corners enclosing two point-symmetric `45`-degree clasping `L`-hook bars separated by uniform negative-space channels.
- **Monochrome Discipline**:
  - Dark surfaces (`brand/logo-mark.svg`, `brand/logo-wordmark-dark.svg`): Pure Chalk White `#F8FAFC` on Matte Tatami `#0B0E13`.
  - Light surfaces (`brand/logo-wordmark-light.svg`): Pure Obsidian `#0B0E13` on Chalk White `#F8FAFC`.
- **Clearspace**: Maintain at least `0.25x` mark height around the diamond vertices with zero curves, gradients, or multi-color fills inside the mark.

## 3. Jiu-Jitsu 5-Belt Color Narrative & Surface Tokens

### Mat Surfaces & Borders
| Token | Hex | Usage |
| :--- | :--- | :--- |
| `--bg-tatami` | `#0B0E13` | Primary matte arena canvas |
| `--bg-card` | `#131821` | Scoreboard strip, exchange cards, and slide-over drawer |
| `--bg-elevated` | `#19202C` | Active exchange highlight and hover surface |
| `--border-hairline` | `#222B3A` | 1px structural dividers |

### Authentic 5-Belt Rank Progression (`brand/belt-ranks.svg`)
| Belt Rank | Hex | Rank Bar Detail |
| :--- | :--- | :--- |
| **White Belt** | `#F8FAFC` | `#11151C` black rank tip with crisp white stripe ticks |
| **Blue Belt** | `#2563EB` | `#11151C` black rank tip |
| **Purple Belt** | `#7C3AED` | `#11151C` black rank tip |
| **Brown Belt** | `#854D0E` | `#11151C` black rank tip |
| **Black Belt** | `#11151C` | `#DC2626` traditional red rank bar bordered by white collars |

### Scoreboard Signal Accents
- **Points (`PTS`)**: `#F8FAFC` high-contrast primary tabular numeral (`#10B981` highlight strictly for `3.0s CONTROL` confirmation and submission win badges).
- **Advantages (`ADV`)**: `#F59E0B` warm amber numeral.
- **Penalties (`PEN`)**: `#EF4444` crisp crimson numeral.

## 4. Typography Scale

- **Brand & UI Headings**: `Space Grotesk` (`600`/`700`, tight `-0.02em` tracking) with `Inter` / system geometric sans body copy (`400`/`500`).
- **Scoreboard Clocks, Counters & Timestamps**: `JetBrains Mono` with `font-variant-numeric: tabular-nums` so countdown clocks (`05:00` -> `02:09`) and `PTS / ADV / PEN` columns never jitter during playback.

## 5. Motion & Synced Telemetry Grammar

- **Video-Synced Scoreboard**: As match video plays or scrubs, the countdown clock, athlete scores, and active exchange card update deterministically from `video.currentTime`.
- **3.0s Control Badge**: Appears cleanly in the scoreboard header during stabilized IBJJF scoring holds without multi-bar progress clutter.
- **On-Demand Visual Study**: `3-Panel Strip` (`1860x614` freeze-frame sequence at `t-1.5s`, `t`, `t+1.5s`) and `0.4x Slow-Mo` slice directly from the active match MP4 and open in a quiet, distraction-free lightbox.
