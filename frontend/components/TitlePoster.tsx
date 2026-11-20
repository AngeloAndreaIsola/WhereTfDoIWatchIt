"use client";

import { useEffect, useState } from "react";
import Image from "next/image";

const images = "https://image.tmdb.org/t/p/w500";
const pending = new Map<string, Promise<string | null>>();
let queue: Promise<unknown> = Promise.resolve();
let waitUntil = 0;

function imageUrl(path: string) {
  const value = path.trim();
  if (/^\/[a-z0-9_-]+\.(jpg|jpeg|png|webp)$/i.test(value)) return images + value;
  try {
    const url = new URL(value);
    if (url.protocol === "https:" || url.protocol === "http:") return url.href;
  } catch {
    return null;
  }
  return null;
}

function lookupPoster(tmdbId: number, contentType: string, posterPath: string) {
  const key = JSON.stringify([tmdbId, contentType, posterPath]);
  const existing = pending.get(key);
  if (existing) return existing;

  const task = queue.then(async () => {
    if (Date.now() < waitUntil) return null;
    for (let attempt = 0; attempt < 3; attempt++) {
      const response = await fetch(`/api/movie/${tmdbId}/poster/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(10000),
        body: JSON.stringify({ content_type: contentType, poster_path: posterPath }),
      });
      if (response.status === 429) {
        const seconds = Number(response.headers.get("Retry-After")) || 60;
        waitUntil = Date.now() + Math.min(86400, Math.max(1, seconds)) * 1000;
        return null;
      }
      if (response.status === 503 && response.headers.get("Retry-After") === "1" && attempt < 2) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        continue;
      }
      if (!response.ok) return null;
      const result = await response.json();
      return typeof result.poster_path === "string" ? imageUrl(result.poster_path) : null;
    }
    return null;
  }).catch(() => null).finally(() => pending.delete(key));
  queue = task;
  pending.set(key, task);
  return task;
}

interface Props {
  tmdbId?: number | null;
  contentType?: string;
  posterPath?: string | null;
  title: string;
  optimized?: boolean;
}

export default function TitlePoster({ tmdbId, contentType, posterPath, title, optimized }: Props) {
  const path = posterPath ?? "";
  const key = JSON.stringify([tmdbId, contentType, path]);
  const original = imageUrl(path);
  const [failedOriginal, setFailedOriginal] = useState<string>();
  const [replacement, setReplacement] = useState<{ key: string; url: string }>();
  const originalUsable = original !== null && failedOriginal !== key;
  const url = originalUsable ? original : (replacement?.key === key ? replacement.url : null);

  useEffect(() => {
    if (originalUsable || !tmdbId || (contentType !== "movie" && contentType !== "series")) {
      return;
    }
    let ignore = false;
    lookupPoster(tmdbId, contentType, path).then((newUrl) => {
      if (!ignore && newUrl && newUrl !== original) {
        setReplacement({ key, url: newUrl });
      }
    });
    return () => { ignore = true; };
  }, [key, tmdbId, contentType, path, original, originalUsable]);

  if (!url && optimized) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center p-2 text-center text-xs text-gray-400 dark:text-gray-500">
        <span className="mb-1 text-2xl">🎬</span>
        <span>No Image</span>
      </div>
    );
  }
  if (!url) {
    return (
      <div className="w-full h-full flex items-center justify-center text-gray-400 text-xs">
        No Poster
      </div>
    );
  }

  function handleError() {
    if (originalUsable) setFailedOriginal(key);
    else setReplacement(undefined);
  }

  if (optimized && url.startsWith(images)) {
    return (
      <Image
        src={url}
        alt={title}
        fill
        sizes="(max-width: 640px) 50vw, (max-width: 768px) 33vw, (max-width: 1024px) 25vw, 20vw"
        className="object-cover transition-transform duration-300 group-hover:scale-105"
        onError={handleError}
      />
    );
  }

  // eslint-disable-next-line @next/next/no-img-element
  return <img src={url} alt={title} className="w-full h-full object-cover" onError={handleError} />;
}
