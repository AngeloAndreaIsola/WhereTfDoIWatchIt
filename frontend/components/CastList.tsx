"use client";

import { useId, useState } from "react";
import type { TMDbCastMember } from "@/lib/api";

/** How many cast members show before the expand/collapse control. */
const COLLAPSED_COUNT = 8;

interface CastListProps {
  cast?: TMDbCastMember[] | null;
}

function personLabel(person: TMDbCastMember): string {
  return person.character ? `${person.name} as ${person.character}` : person.name;
}

function hasUsableId(person: TMDbCastMember): boolean {
  return Number.isInteger(person.id) && person.id > 0;
}

export default function CastList({ cast }: CastListProps) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();

  if (!cast || cast.length === 0) return null;

  const collapsible = cast.length > COLLAPSED_COUNT;
  const visible = expanded || !collapsible ? cast : cast.slice(0, COLLAPSED_COUNT);

  return (
    <div className="mt-3">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
        Cast
      </h3>
      <div id={listId} className="mt-1 flex flex-wrap gap-1.5">
        {visible.map((person, index) => {
          const label = personLabel(person);
          const badgeClass =
            "rounded-full bg-blue-50 px-2.5 py-0.5 text-xs text-blue-700 dark:bg-blue-900/30 dark:text-blue-300";
          // TMDb can repeat a person id across roles; index keeps keys unique.
          const key = `${person.id}-${index}`;
          return hasUsableId(person) ? (
            <a
              key={key}
              href={`https://www.themoviedb.org/person/${person.id}`}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`${person.name} on TMDb${person.character ? ` (${person.character})` : ""}`}
              className={`${badgeClass} transition-colors hover:bg-blue-100 hover:underline dark:hover:bg-blue-900/50`}
            >
              {label}
            </a>
          ) : (
            <span key={key} className={badgeClass}>
              {label}
            </span>
          );
        })}
        {collapsible && (
          <button
            type="button"
            onClick={() => setExpanded((prev) => !prev)}
            aria-expanded={expanded}
            aria-controls={listId}
            className="text-xs font-medium text-blue-600 hover:underline dark:text-blue-400"
          >
            {expanded
              ? "Show less"
              : `Show all cast (${cast.length})`}
          </button>
        )}
      </div>
    </div>
  );
}
