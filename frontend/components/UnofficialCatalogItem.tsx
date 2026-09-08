"use client";

import Link from "next/link";
import LanguageBadge from "@/components/LanguageBadge";

interface UnofficialCatalogItemProps {
  item: {
    id: number;
    scraped_title: string;
    scraped_year: number | null;
    scraped_url: string;
    provider: {
      slug: string;
      name: string;
      source_indicator: string;
      domain: string;
      is_stale: boolean;
    };
    movie: {
      tmdb_id: number;
      title: string;
      year: number | null;
      poster_path: string;
    } | null;
    is_confirmed: boolean;
    confidence_score: number;
    raw_payload: Record<string, unknown>;
    is_available: boolean;
    source_indicator: string;
  };
  preferredAudio?: string[];
  preferredSubtitles?: string[];
}

const SOURCE_BADGE_COLORS: Record<string, string> = {
  Unofficial:
    "bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-300",
  Free:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-300",
  BVOD:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-300",
  AVOD:
    "bg-teal-100 text-teal-800 dark:bg-teal-900/60 dark:text-teal-300",
  FAST:
    "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/60 dark:text-cyan-300",
  Official:
    "bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300",
  SVOD:
    "bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300",
  TVOD:
    "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-300",
  Scraper:
    "bg-purple-100 text-purple-800 dark:bg-purple-900/60 dark:text-purple-300",
};

function qualityLabel(q: string): string {
  const map: Record<string, string> = {
    "4k": "4K",
    uhd: "4K",
    hdr: "HDR",
    hd: "HD",
    "1080p": "1080p",
    "720p": "720p",
    "480p": "480p",
    sd: "SD",
  };
  return map[q.toLowerCase()] ?? q.toUpperCase();
}

export default function UnofficialCatalogItem({
  item,
  preferredAudio,
  preferredSubtitles,
}: UnofficialCatalogItemProps) {
  const payload = item.raw_payload || {};
  const audioLangs: string[] = Array.isArray(payload.audio_languages)
    ? payload.audio_languages.map((l) => String(l).toUpperCase())
    : Array.isArray(payload.audio)
    ? payload.audio.map((l) => String(l).toUpperCase())
    : [];
  const subLangs: string[] = Array.isArray(payload.subtitle_languages)
    ? payload.subtitle_languages.map((l) => String(l).toUpperCase())
    : Array.isArray(payload.subtitles)
    ? payload.subtitles.map((l) => String(l).toUpperCase())
    : [];
  const quality = (payload.quality as string) || item.provider?.slug || "";
  const movie = item.movie;
  const badgeColor =
    SOURCE_BADGE_COLORS[item.source_indicator] ||
    SOURCE_BADGE_COLORS["Unofficial"];
  const prefAudioSet = new Set(
    (preferredAudio || []).map((c) => c.toUpperCase())
  );
  const prefSubSet = new Set(
    (preferredSubtitles || []).map((c) => c.toUpperCase())
  );

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow flex flex-col">
      {/* Poster or placeholder */}
      <div className="aspect-[2/3] bg-gray-100 dark:bg-gray-800 relative">
        {movie?.poster_path ? (
          <img
            src={`https://image.tmdb.org/t/p/w500${movie.poster_path}`}
            alt={item.scraped_title}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex flex-col items-center justify-center text-gray-400 text-xs gap-1">
            <span className="text-2xl">🎬</span>
            <span>No Poster</span>
          </div>
        )}
        {/* Source badge */}
        <span
          className={`absolute top-2 left-2 px-2 py-0.5 rounded-md text-[10px] font-bold ${badgeColor}`}
        >
          {item.source_indicator}
        </span>
        {/* Availability badge */}
        <span
          className={`absolute top-2 right-2 px-2 py-0.5 rounded-md text-[10px] font-bold ${
            item.is_available
              ? "bg-emerald-500 text-white"
              : "bg-gray-500 text-white"
          }`}
        >
          {item.is_available ? "Available" : "Unavailable"}
        </span>
        {/* Confirmed badge */}
        {item.is_confirmed && (
          <span className="absolute bottom-2 left-2 px-2 py-0.5 rounded-md text-[10px] font-bold bg-indigo-500 text-white">
            ✓ Matched
          </span>
        )}
      </div>

      <div className="p-4 flex-1 flex flex-col justify-between space-y-3">
        <div>
          <h3 className="font-bold text-gray-900 dark:text-gray-100 line-clamp-1">
            {item.scraped_title}
          </h3>
          <p className="text-xs text-gray-500">
            {item.scraped_year && `${item.scraped_year} `}
            {movie ? (
              <span className="text-indigo-600 dark:text-indigo-400">
                → {movie.title}
                {movie.year && ` (${movie.year})`}
              </span>
            ) : (
              <span className="text-gray-400">(unmatched)</span>
            )}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">
            {item.provider.name} · {item.provider.domain}
            {item.provider.is_stale && (
              <span className="text-amber-500 ml-1">⚠ stale</span>
            )}
          </p>
        </div>

        {/* Language info */}
        <div className="space-y-1.5">
          {audioLangs.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-[10px] font-semibold text-gray-500 dark:text-gray-400">
                Audio:
              </span>
              {audioLangs.map((code) => (
                <LanguageBadge
                  key={`audio-${code}`}
                  code={code}
                  type="audio"
                  isPreferred={prefAudioSet.has(code)}
                  compact
                />
              ))}
            </div>
          )}
          {subLangs.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-[10px] font-semibold text-gray-500 dark:text-gray-400">
                Subs:
              </span>
              {subLangs.map((code) => (
                <LanguageBadge
                  key={`sub-${code}`}
                  code={code}
                  type="subtitle"
                  isPreferred={prefSubSet.has(code)}
                  compact
                />
              ))}
            </div>
          )}
          {quality && (
            <div className="flex items-center gap-1">
              <span className="text-[10px] font-semibold text-gray-500 dark:text-gray-400">
                Quality:
              </span>
              <span className="rounded bg-gray-200 px-1.5 py-0.5 font-mono text-[10px] font-bold text-gray-700 dark:bg-gray-600 dark:text-gray-200">
                {qualityLabel(quality)}
              </span>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2">
          {movie && (
            <Link
              href={`/movie/${movie.tmdb_id}`}
              className="flex-1 py-1.5 text-xs font-semibold text-center rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
            >
              Details
            </Link>
          )}
          {item.scraped_url && (
            <a
              href={item.scraped_url}
              target="_blank"
              rel="noreferrer"
              className="py-1.5 px-3 text-xs font-semibold text-center rounded-lg border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
            >
              Watch →
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
