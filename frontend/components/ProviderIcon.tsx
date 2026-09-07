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

    // Free streaming providers (European public broadcasters & global free services)
    case "raiplay":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#003399" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            Rai
          </text>
        </svg>
      );
    case "france_tv":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#001430" />
          <text x="12" y="15" fontSize="7" fontWeight="bold" fill="#00A3E0" textAnchor="middle">
            france.tv
          </text>
        </svg>
      );
    case "ard_zdf":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#002D5A" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FA7D00" textAnchor="middle">
            ZDF
          </text>
        </svg>
      );
    case "rtve_play":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#FF6600" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            RTVE
          </text>
        </svg>
      );
    case "npo_start":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#FF5500" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            NPO
          </text>
        </svg>
      );
    case "vrt_auvio":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#FF0050" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            VRT
          </text>
        </svg>
      );
    case "rtp_play":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#008040" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            RTP
          </text>
        </svg>
      );
    case "svt_play":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#3399FF" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            SVT
          </text>
        </svg>
      );
    case "nrk_tv":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#00A3E0" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            NRK
          </text>
        </svg>
      );
    case "drtv":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#D0021B" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            DR
          </text>
        </svg>
      );
    case "yle_areena":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#00B4B4" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            yle
          </text>
        </svg>
      );
    case "orf_on":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#C80000" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            ORF
          </text>
        </svg>
      );
    case "play_suisse":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#D52B1E" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            SRG
          </text>
        </svg>
      );
    case "ertflix":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#003399" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            ERT
          </text>
        </svg>
      );
    case "rte_player":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#00A099" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            RTÉ
          </text>
        </svg>
      );
    case "bbc_iplayer":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#F54900" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            BBC
          </text>
        </svg>
      );
    case "ivysilani":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#112244" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#00A8FF" textAnchor="middle">
            ČT
          </text>
        </svg>
      );
    case "tvp_vod":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#002B66" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#E60000" textAnchor="middle">
            TVP
          </text>
        </svg>
      );
    case "mediaklikk":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#004488" />
          <text x="12" y="15" fontSize="7" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            Média
          </text>
        </svg>
      );
    case "hrti":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#0033A0" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#D90000" textAnchor="middle">
            HRT
          </text>
        </svg>
      );
    case "pbs":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#005DAB" />
          <text x="12" y="15" fontSize="8" fontWeight="extrabold" fill="#FFFFFF" textAnchor="middle">
            PBS
          </text>
        </svg>
      );
    case "abc_iview":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#008080" />
          <text x="12" y="15" fontSize="7" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            iview
          </text>
        </svg>
      );
    case "mediaset_infinity":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#1C2237" />
          <text x="12" y="15" fontSize="6" fontWeight="bold" fill="#38B6FF" textAnchor="middle">
            Infinity
          </text>
        </svg>
      );
    case "sbs_ondemand":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#E21B23" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            SBS
          </text>
        </svg>
      );
    case "cbc_gem":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#FF0000" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            CBC
          </text>
        </svg>
      );
    case "tubi":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#FA3200" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            tubi
          </text>
        </svg>
      );
    case "pluto_tv":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#000000" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFF00" textAnchor="middle">
            pluto
          </text>
        </svg>
      );
    case "canal_once":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#008080" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            11
          </text>
        </svg>
      );
    case "tv_brasil":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#009B3A" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FEDF00" textAnchor="middle">
            TVB
          </text>
        </svg>
      );
    case "tv_publica":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#74ACDF" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            TVP
          </text>
        </svg>
      );
    case "kbs":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#0055A5" />
          <text x="12" y="15" fontSize="8" fontWeight="bold" fill="#FFFFFF" textAnchor="middle">
            KBS
          </text>
        </svg>
      );
    case "nhk_world":
      return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <rect width="24" height="24" rx="5" fill="#1A1A1A" />
          <text x="12" y="15" fontSize="7" fontWeight="bold" fill="#E60012" textAnchor="middle">
            NHK
          </text>
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
