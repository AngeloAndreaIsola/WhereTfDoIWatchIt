"use client";

import Link from "next/link";
import Image from "next/image";
import LanguageBadge from "@/components/LanguageBadge";

export interface MovieCardData {
  tmdb_id: number;
  title: string;
  year?: number | null;
  poster_path?: string | null;
  categories?: string[];
  best_service?: string | null;
  best_country?: string | null;
  audio_languages?: string[];
  subtitle_languages?: string[];
  preferred_audio?: string[];
  preferred_subtitles?: string[];
  is_watched?: boolean;
  rating?: number | null;
  is_in_watchlist?: boolean;
}

interface MovieCardProps {
  movie: MovieCardData;
}

export default function MovieCard({ movie }: MovieCardProps) {
  const posterUrl = movie.poster_path
    ? (movie.poster_path.startsWith("http")
        ? movie.poster_path
        : `https://image.tmdb.org/t/p/w500${movie.poster_path}`)
    : null;

  const prefAudioSet = new Set(
    (movie.preferred_audio || []).map((c) => c.toUpperCase())
  );
  const prefSubSet = new Set(
    (movie.preferred_subtitles || []).map((c) => c.toUpperCase())
  );

  const audioLangs = (movie.audio_languages || []).map((c) => c.toUpperCase());
  const subLangs = (movie.subtitle_languages || []).map((c) => c.toUpperCase());

  const matchingAudio = audioLangs.filter((c) => prefAudioSet.has(c));
  const matchingSubs = subLangs.filter((c) => prefSubSet.has(c));

  return (
    <Link
      href={`/movie/${movie.tmdb_id}`}
      className="group flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white text-decoration-none shadow-sm transition-all hover:-translate-y-1 hover:shadow-lg dark:border-gray-700 dark:bg-gray-800"
    >
      {/* Poster Image */}
      <div className="relative aspect-[2/3] w-full overflow-hidden bg-gray-200 dark:bg-gray-700">
        {posterUrl ? (
          <Image
            src={posterUrl}
            alt={movie.title}
            fill
            sizes="(max-width: 640px) 50vw, (max-width: 768px) 33vw, (max-width: 1024px) 25vw, 20vw"
            className="object-cover transition-transform duration-300 group-hover:scale-105"
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center p-2 text-center text-xs text-gray-400 dark:text-gray-500">
            <span className="mb-1 text-2xl">🎬</span>
            <span>No Image</span>
          </div>
        )}

        {/* Top Badges Overlay */}
        <div className="absolute top-2 left-2 right-2 flex flex-wrap justify-between gap-1 pointer-events-none">
          {movie.best_service && (
            <span className="rounded-md bg-black/70 px-2 py-0.5 text-[10px] font-semibold text-white backdrop-blur-xs">
              {movie.best_service} {movie.best_country ? `(${movie.best_country})` : ""}
            </span>
          )}

          {movie.rating && (
            <span className="ml-auto rounded-md bg-amber-500/90 px-1.5 py-0.5 text-[10px] font-bold text-white shadow-xs">
              ★ {movie.rating}
            </span>
          )}
        </div>

        {movie.is_watched && (
          <div className="absolute bottom-2 right-2 rounded-full bg-emerald-600 px-2 py-0.5 text-[10px] font-bold text-white shadow-xs">
            ✓ Watched
          </div>
        )}
      </div>

      {/* Card Content */}
      <div className="flex flex-1 flex-col justify-between p-3">
        <div>
          <h3 className="line-clamp-2 text-sm font-semibold text-gray-900 group-hover:text-blue-600 dark:text-gray-100 dark:group-hover:text-blue-400">
            {movie.title}
          </h3>
          {movie.year && (
            <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
              {movie.year}
            </p>
          )}
        </div>

        {/* Language Preference Highlights */}
        {(matchingAudio.length > 0 || matchingSubs.length > 0) && (
          <div className="mt-2.5 flex flex-wrap gap-1 border-t border-gray-100 pt-2 dark:border-gray-700/60">
            {matchingAudio.map((code) => (
              <LanguageBadge
                key={`audio-${code}`}
                code={code}
                type="audio"
                isPreferred={true}
                compact={true}
              />
            ))}
            {matchingSubs.map((code) => (
              <LanguageBadge
                key={`sub-${code}`}
                code={code}
                type="subtitle"
                isPreferred={true}
                compact={true}
              />
            ))}
          </div>
        )}

        {/* Categories if present */}
        {movie.categories && movie.categories.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {movie.categories.slice(0, 2).map((cat) => (
              <span
                key={cat}
                className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-600 dark:bg-gray-700 dark:text-gray-300"
              >
                {cat}
              </span>
            ))}
          </div>
        )}
      </div>
    </Link>
  );
}
