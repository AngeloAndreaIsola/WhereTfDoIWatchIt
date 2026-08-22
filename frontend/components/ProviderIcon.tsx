import React from "react";

interface ProviderIconProps {
  slug: string;
  name: string;
  className?: string;
}

export default function ProviderIcon({ slug, name, className = "h-8 w-8" }: ProviderIconProps) {
  const cleanSlug = (slug || "").toLowerCase();

  switch (cleanSlug) {
    case "prime":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#00A8E1" />
          <text x="12" y="16" fontSize="9" fontWeight="extrabold" fill="#FFFFFF" textAnchor="middle">
            prime
          </text>
        </svg>
      );
    case "netflix":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#E50914" />
          <text x="12" y="16" fontSize="12" fontWeight="black" fill="#FFFFFF" textAnchor="middle">
            N
          </text>
        </svg>
      );
    case "disney":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#113CCF" />
          <text x="12" y="16" fontSize="11" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            D+
          </text>
        </svg>
      );
    case "hulu":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#1CE783" />
          <text x="12" y="16" fontSize="10" fontWeight="extrabold" fill="#0B0C0D" textAnchor="middle">
            hulu
          </text>
        </svg>
      );
    case "max":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#002BE7" />
          <text x="12" y="16" fontSize="11" fontWeight="extrabold" fill="#FFFFFF" textAnchor="middle">
            MAX
          </text>
        </svg>
      );
    case "apple":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#000000" />
          <text x="12" y="16" fontSize="10" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            tv+
          </text>
        </svg>
      );
    case "paramount":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#0064FF" />
          <text x="12" y="16" fontSize="11" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            P+
          </text>
        </svg>
      );
    case "peacock":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#000000" />
          <circle cx="12" cy="12" r="6" fill="#00A859" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            P
          </text>
        </svg>
      );
    case "youtubetv":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#FF0000" />
          <polygon points="10,8 16,12 10,16" fill="#FFFFFF" />
        </svg>
      );
    case "crunchyroll":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#FF6600" />
          <circle cx="12" cy="12" r="6" fill="#FFFFFF" />
          <circle cx="14" cy="12" r="4" fill="#FF6600" />
        </svg>
      );
    default:
      return (
        <div
          className={`${className} flex items-center justify-center rounded bg-gray-200 text-xs font-bold text-gray-700 dark:bg-gray-700 dark:text-gray-200`}
        >
          {name.slice(0, 2).toUpperCase()}
        </div>
      );
  }
}
