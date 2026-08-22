"use client";

import Link from "next/link";
import Image from "next/image";
import type { PopularMovie } from "@/lib/api";

export { PopularMovie };

interface MovieCardProps {
  movie: PopularMovie;
}

function CategoryChips({ categories }: { categories: string[] }) {
  if (!categories.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {categories.slice(0, 3).map((cat) => (
        <span
          key={cat}
          className="rounded-full bg-gray-100 px-1.5 py-0.5 text-xs font-medium text-gray-700 dark:bg-gray-700 dark:text-gray-300"
        >
          {cat}
        </span>
      ))}
    </div>
  );
}

export default function MovieCard({ movie }: MovieCardProps) {
  const posterUrl = movie.poster_path
    ? `https://image.tmdb.org/t/p/w500${movie.poster_path}`
    : null;

  return (
    <Link
      href={`/movie/${movie.tmdb_id}`}
      className="group block rounded-lg border border-gray-200 bg-white text-decoration-none shadow-sm transition-all hover:scale-[1.02] hover:shadow-md dark:border-gray-700 dark:bg-gray-800"
    >
      <div className="relative aspect-[2/3] overflow-hidden rounded-t-lg bg-gray-200 dark:bg-gray-700">
        {posterUrl ? (
          <Image
            src={posterUrl}
            alt={movie.title}
            fill
            sizes="(max-width: 768px) 40vw, (max-width: 1024px) 25vw, 20vw"
            className="object-cover transition-transform group-hover:scale-105"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-gray-400 dark:text-gray-500">
            No image
          </div>
        )}
      </div>
      <div className="p-3">
        <h3 className="font-semibold text-gray-900 dark:text-gray-100">{movie.title}</h3>
        {movie.year && (
          <p className="text-xs text-gray-500 dark:text-gray-400">{movie.year}</p>
        )}
        <CategoryChips categories={movie.categories} />
      </div>
    </Link>
  );
}
