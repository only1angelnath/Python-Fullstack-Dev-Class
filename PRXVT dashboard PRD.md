# PRXVT Analytics Dashboard — Product Requirements Document

**Version:** 1.0  
**Project:** PRXVT Token Analytics Frontend  
**Network:** Base (via Uniswap v2/v4)  
**Data source:** Dune Analytics, served via FastAPI at `https://classtut.up.railway.app`

---

## 1. Product Overview

A single-page analytics dashboard for the PRXVT privacy token. The frontend fetches live data from five FastAPI endpoints and presents it in an interactive, data-dense interface built for sophisticated users (DeFi participants, token holders, research-oriented visitors). All data is sourced exclusively from the `rows` key of each API response. No mock data, no hallucinated values.

**Primary goal:** Give users an accurate, real-time snapshot of token health, price trajectory, holder concentration, staking participation, and liquidity depth.

---

## 2. Design System

### 2.1 Color Tokens

| Token | Hex | Usage |
|---|---|---|
| `--bg` | `#080808` | Page background |
| `--surface-1` | `#0e0e0e` | Card / panel background |
| `--surface-2` | `#141414` | Nested card, input background |
| `--surface-3` | `#1a1a1a` | Active state, badge background |
| `--border` | `#1e1e1e` | All borders (default) |
| `--border-hover` | `#2c2c2c` | Border on hover |
| `--text-primary` | `#f2f2f2` | Headlines, key values |
| `--text-secondary` | `#5a5a5a` | Labels, captions |
| `--text-muted` | `#2e2e2e` | Progress bar fill, decoration |
| `--green` | `#22c55e` | Positive price change, live dot |
| `--red` | `#ef4444` | Negative price change |
| `--accent` | `#c8c8c8` | Progress bar fill, chart line highlight |

### 2.2 Typography

| Role | Family | Weight | Size |
|---|---|---|---|
| Display / Headlines | Syne | 700, 800 | 14–26px |
| Data values | IBM Plex Mono | 400, 500 | 10–15px |
| Labels | IBM Plex Mono | 300 | 10–11px, uppercase, 0.08em tracking |
| Body | Syne | 400 | 13–14px |

No system fonts. No Inter. No generic sans-serif fallbacks in production code.

### 2.3 Spacing Scale

Base unit: `4px`  
Scale: `4 / 8 / 12 / 16 / 20 / 24 / 32 / 48`

All padding and gap values must reference this scale. No arbitrary values.

### 2.4 Border Radius

| Context | Value |
|---|---|
| Cards / sections | `8px` |
| Badges, toggles, small UI | `4–6px` |
| Progress bars | `2px` |
| Live dot | `50%` |

### 2.5 Animation Tokens

| Name | Spec | Purpose |
|---|---|---|
| `fadeUp` | `0.5s ease, translateY(12px -> 0)` | Stat card entrance |
| `shimmer` | `1.6s ease infinite, bg-position sweep` | Skeleton loading |
| `pulse-dot` | `2s ease infinite, opacity 1->0.3->1` | Live indicator |
| Chart transition | `0.3s ease` | Mode / range toggle |
| Hover state | `0.15–0.2s ease` | Border, color changes |

**Stagger rule:** Stat cards enter with `60ms` delay between each (index * 60ms). Holder/staker rows stagger at `60ms`. Pool cards at `80ms`.

**Easing:** All transitions use `ease` or `ease-out`. No `linear` except shimmer sweep. No `ease-in` for entrances.

---

## 3. Component Architecture

```
PRXVTDashboard (root)
├── GlobalStyles          # Font injection, keyframes, resets
├── Header                # Logo, live indicator, price ticker
├── main
│   ├── OverviewGrid      # 6 StatCard components
│   ├── PriceSection      # ComposedChart + ToggleGroup (mode) + ToggleGroup (range)
│   ├── [grid 2-col]
│   │   ├── HoldersSection  # Ranked list + BarProgress
│   │   └── StakersSection  # Ranked list + BarProgress
│   └── LiquiditySection  # 3-column pool cards grid
└── Footer
```

### Shared primitives

- `Sk` — Skeleton placeholder (width, height, borderRadius props)
- `StatCard` — Label, value, sub-value with loading state
- `ToggleGroup` — Pill-style multi-option selector
- `Section` — Titled panel wrapper with optional badge and action slot
- `BarProgress` — Horizontal fill bar (percentage-relative)
- `ChartTooltip` — Custom recharts tooltip

---

## 4. Data Endpoints and Field Mapping

All responses return `{ metadata: {...}, rows: [...] }`. The dashboard reads **only `rows`**.

### 4.1 `/data/prxvttokenoverview` — `rows[0]`

| Field | Display label | Format |
|---|---|---|
| `Market Cap` | Market Cap | USD (compact) |
| `FDV` | FDV | USD (compact) |
| `price` | Token Price | 8 decimal places |
| `volume_24h_usd` | 24H Volume | USD (compact) |
| `unique_circulating_holders` | Unique Holders | integer |
| `total_supply` | Total Supply | token compact |
| `circulating_pct` | sub-label on Market Cap | "X.X% circulating" |
| `top_10_pct_of_circulating` | sub-label on Unique Holders | "Top 10 hold X.X%" |
| `total_locked_or_burned` | sub-label on Total Supply | "X locked/burned" |

### 4.2 `/data/prxvttokenprice` — `rows[]` (reversed to ascending order)

| Field | Chart series | Format |
|---|---|---|
| `day` | X-axis | "MMM D" |
| `Price USD` | Line (PRICE mode) | 8 decimal USD |
| `Daily DEX Vol USD` | Bar (VOLUME mode) | compact USD |

Chart mode toggle: `PRICE` / `VOLUME` / `COMBINED`. Time range: `7D` / `30D` / `90D` / `ALL`.

### 4.3 `/data/prxvttopholders` — `rows[]`

| Field | Display | Notes |
|---|---|---|
| `Rank` | Row number | |
| `wallet` | Truncated address | `0x????...????` linked to Basescan |
| `Percentage of Holding` | Percentage + bar fill | Max = first holder's pct |
| `Value` | Right-aligned USD | |
| `Amount Held` | Sub-value in PRXVT | compact token format |

### 4.4 `/data/prxvtstakers` — `rows[]`

| Field | Display | Notes |
|---|---|---|
| `rank` | Row number | |
| `user` | Truncated address | Linked to Basescan |
| `percentage_of_total` | Percentage + bar fill | Max = first staker's pct |
| `Value USD` | Right-aligned USD | |
| `current_staked` | Sub-value in PRXVT | compact token format |

### 4.5 `/data/prxvtliquiditypools` — `rows[]`

| Field | Display | Notes |
|---|---|---|
| `Pool Name` | Card title | |
| `Exchange` | Badge below title | |
| `24H % Price Change` | Colored badge (green/red/gray) | sign prefix |
| `Liquidity` | Grid cell | compact USD |
| `24H Volume` | Grid cell | compact USD |
| `Token Price` | Grid cell | 8 decimal USD |
| `FDV` | Grid cell | compact USD |
| `Pool Address` | Truncated link | Basescan |
| `Link` | "GeckoTerminal ↗" | Parsed from HTML string |

---

## 5. Interaction Specifications

### 5.1 Chart Toggle (PRICE / VOLUME / COMBINED)

- Active tab: filled `--surface-3` background, `1px solid --border`, `--text-primary`
- Inactive tab: transparent, `--text-secondary`
- Switching mode rerenders the chart without remounting; series appear/disappear via conditional rendering
- In COMBINED mode, dual Y-axes: Price left, Volume right

### 5.2 Time Range Filter (7D / 30D / 90D / ALL)

- Filters `rows` client-side by comparing `day` timestamp to `Date.now() - N * 864e5`
- Badge on section header updates to show filtered data point count
- Computed via `useMemo` to avoid redundant recalculation

### 5.3 Address Links

- Format: `0xa79f...e16` (first 6 chars, ellipsis, last 4 chars)
- Destination: `https://basescan.org/address/{fullAddress}`
- `target="_blank" rel="noopener noreferrer"` on all external links
- Hover: color transitions from `--text-secondary` to `--text-primary`

### 5.4 Row Hover States

- Holder and staker rows: background transitions to `--surface-2` on hover
- Transition: `0.15s ease`
- No click action — hover is purely visual feedback

### 5.5 Pool Cards

- Each card shows: pool name, exchange, 24H price change badge, 4-cell data grid, pool address link, GeckoTerminal link
- Price change color: positive = `--green`, negative = `--red`, zero = `--text-secondary`

---

## 6. Loading and Error States

### Skeleton screens

All data sections render skeleton placeholders while their fetch is in flight. Skeletons match the approximate shape of real content:
- Stat cards: two skeleton bars (value + sub-label)
- Chart: full-height skeleton block
- Holder/staker rows: 5 skeleton bars
- Liquidity pool cards: 3 skeleton cards

### CORS error banner

If any fetch fails (network error or CORS block), a persistent amber warning banner renders below the header:

> `CORS issue detected. Add allow_origins=["*"] to your FastAPI CORSMiddleware.`

This does not block rendering — sections with successful fetches still display normally.

### Empty state

If `rows` returns an empty array, sections show a centered mono-font message: `No data available`.

---

## 7. Backend Setup Requirements

Add the following to your FastAPI application before registering routes:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # restrict to your frontend domain in production
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)
```

Without this, browser clients will receive CORS errors and all fetches will fail silently.

---

## 8. Implementation Stack

| Concern | Choice |
|---|---|
| Framework | React 18 (functional components, hooks) |
| Charts | Recharts `ComposedChart` |
| Styling | Inline JS styles + injected CSS keyframes |
| Fonts | Google Fonts (Syne + IBM Plex Mono) via `<link>` injection |
| State | `useState`, `useMemo`, `useEffect` |
| No external state manager | Context not needed at this scale |
| No localStorage | All state is in-memory per session |

---

## 9. Copywriting Rules

- No em dashes anywhere in UI copy
- No vague filler phrases ("powerful analytics", "seamless experience")
- No placeholder testimonials
- All numeric values formatted precisely as specified in section 4
- Disclaimer in footer: "Not financial advice"
- Data attribution: "Data sourced from Dune Analytics via Base Network"

---

## 10. Out of Scope (v1)

- Wallet search / lookup
- Historical staker/holder charts
- Token transfer feed
- Price alerts
- Dark/light mode toggle (dark only)
- Multi-token support