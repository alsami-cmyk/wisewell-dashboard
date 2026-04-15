export default function AdnocLogo() {
  return (
    <svg
      className="w-11 h-11 flex-shrink-0"
      viewBox="0 0 44 44"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="ADNOC Distribution"
    >
      <circle
        cx="22" cy="22" r="20"
        fill="rgba(255,255,255,0.06)"
        stroke="rgba(255,255,255,0.2)"
        strokeWidth="1"
      />
      {/* Stylised falcon mark */}
      <path
        d="M22 8C22 8 16 11 14 16C12 21 14 26 18 28C16 30 14 32 14 32C14 32 18 33 22 31C26 33 30 32 30 32C30 32 28 30 26 28C30 26 32 21 30 16C28 11 22 8 22 8Z"
        fill="#E8B84B"
        opacity="0.9"
      />
      <ellipse cx="19" cy="19" rx="2" ry="2.5" fill="#0D1B3E" opacity="0.6" />
      <ellipse cx="25" cy="19" rx="2" ry="2.5" fill="#0D1B3E" opacity="0.6" />
    </svg>
  )
}
