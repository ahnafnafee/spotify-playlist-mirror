import { useId } from 'react'

export type ServiceId = 'spotify' | 'apple' | 'ytmusic' | 'jellyfin'

interface ServiceLogoProps {
  service: ServiceId
  className?: string
}

/** Full-colour brand marks for the connected services, rendered as
 * self-contained SVGs in each service's own colours (no external/CDN image
 * assets). Shown to identify which third-party service a card/row belongs to —
 * nominative use. Colour is baked into each mark, so a text-* class on a parent
 * has no effect; size via `className` (e.g. `size-8`). */
export function ServiceLogo({ service, className }: ServiceLogoProps) {
  switch (service) {
    case 'spotify':
      return <SpotifyMark className={className} />
    case 'apple':
      return <AppleMusicMark className={className} />
    case 'ytmusic':
      return <YouTubeMusicMark className={className} />
    case 'jellyfin':
      return <JellyfinMark className={className} />
  }
}

function SpotifyMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} role="img" aria-label="Spotify">
      <circle cx="12" cy="12" r="12" fill="#1DB954" />
      <g fill="none" stroke="#0B1E12" strokeLinecap="round">
        <path d="M5.7 9c4-1.15 9-.8 12.4 1.25" strokeWidth="2" />
        <path d="M6.5 12.3c3.4-.95 7.7-.65 10.6 1.1" strokeWidth="1.7" />
        <path d="M7.2 15.4c2.8-.75 6.1-.5 8.4.95" strokeWidth="1.4" />
      </g>
    </svg>
  )
}

function AppleMusicMark({ className }: { className?: string }) {
  const g = useId()
  return (
    <svg viewBox="0 0 24 24" className={className} role="img" aria-label="Apple Music">
      <defs>
        <linearGradient id={g} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#FA586A" />
          <stop offset="1" stopColor="#F5455F" />
        </linearGradient>
      </defs>
      <rect width="24" height="24" rx="6" fill={`url(#${g})`} />
      <g fill="#fff">
        <path d="M9.1 8.8 14.8 7.35V9.2L9.1 10.65Z" />
        <rect x="13.35" y="7.1" width="1.45" height="7.4" rx="0.5" />
        <rect x="9.1" y="8.4" width="1.45" height="7.5" rx="0.5" />
        <ellipse cx="8.35" cy="15.9" rx="2.05" ry="1.65" />
        <ellipse cx="12.6" cy="14.5" rx="2.05" ry="1.65" />
      </g>
    </svg>
  )
}

function YouTubeMusicMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} role="img" aria-label="YouTube Music">
      <circle cx="12" cy="12" r="12" fill="#FF0000" />
      <circle cx="12" cy="12" r="6.4" fill="none" stroke="#fff" strokeWidth="1.5" />
      <path d="M10.35 9.05 15.4 12l-5.05 2.95V9.05Z" fill="#fff" />
    </svg>
  )
}

function JellyfinMark({ className }: { className?: string }) {
  const g = useId()
  return (
    <svg viewBox="0 0 24 24" className={className} role="img" aria-label="Jellyfin">
      <defs>
        <linearGradient id={g} x1="0.15" y1="0" x2="0.85" y2="1">
          <stop offset="0" stopColor="#AA5CC3" />
          <stop offset="1" stopColor="#00A4DC" />
        </linearGradient>
      </defs>
      {/* two overlapping "petal" chevrons — the stylised jellyfin mark */}
      <path
        d="M12 3.4c-1.15 0-4.6 5.9-4.05 7.75.5 1.7 7.6 1.7 8.1 0C16.6 9.3 13.15 3.4 12 3.4Z"
        fill={`url(#${g})`}
      />
      <path
        d="M12 20.6c1.15 0 4.6-5.9 4.05-7.75-.5-1.7-7.6-1.7-8.1 0C7.4 14.7 10.85 20.6 12 20.6Z"
        fill={`url(#${g})`}
        opacity="0.72"
      />
    </svg>
  )
}
