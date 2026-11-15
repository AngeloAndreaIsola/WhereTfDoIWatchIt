"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { MouseEvent, ReactNode } from "react";
import { stashAmbiguousItems } from "@/lib/ambiguousReview";

interface AmbiguousReviewLinkProps<T> {
  items: T[];
  className?: string;
  children: ReactNode;
}

/**
 * Link to the ambiguous-match review page.
 *
 * Items are passed through sessionStorage instead of the query string: the
 * legacy `?items=<json>` payload blows past the server's header size limit as
 * soon as an import produces more than a handful of ambiguous rows (HTTP 431).
 * If sessionStorage is unavailable, the link falls back to the query string.
 */
export default function AmbiguousReviewLink<T>({
  items,
  className,
  children,
}: AmbiguousReviewLinkProps<T>) {
  const router = useRouter();
  const href = "/watchlist/ambiguous";

  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    try {
      stashAmbiguousItems(items);
    } catch {
      event.preventDefault();
      router.push(
        `${href}?items=${encodeURIComponent(JSON.stringify(items))}`
      );
    }
  };

  return (
    <Link href={href} onClick={handleClick} className={className}>
      {children}
    </Link>
  );
}
