import { useEffect, useMemo, useState, type SyntheticEvent } from "react";
import {
  globalSearch,
  recordSearchSelection,
  type GlobalSearchResultGroup,
  type GlobalSearchResultItem,
} from "../lib/api";
import { AsyncState } from "./AsyncState";

type SearchFilter = "all" | "media" | "episode" | "podcast";

interface SearchExperienceProps {
  initialQuery?: string;
  initialFilter?: SearchFilter;
}

const filters: { id: SearchFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "media", label: "References" },
  { id: "episode", label: "Episodes" },
  { id: "podcast", label: "Sources" },
];

const groupLabels: Record<GlobalSearchResultGroup["type"], string> = {
  media: "References",
  episode: "Episodes",
  podcast: "Sources",
};

export function SearchExperience({
  initialQuery = "",
  initialFilter = "all",
}: SearchExperienceProps) {
  const [input, setInput] = useState(initialQuery);
  const [query, setQuery] = useState(initialQuery);
  const [filter, setFilter] = useState<SearchFilter>(initialFilter);
  const [groups, setGroups] = useState<GlobalSearchResultGroup[]>([]);
  const [loading, setLoading] = useState(Boolean(initialQuery));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!query) {
      setGroups([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    globalSearch(query, 20)
      .then((response) => setGroups(response.results))
      .catch(() => setError("Search is temporarily unavailable."))
      .finally(() => setLoading(false));
  }, [query]);

  const visibleGroups = useMemo(
    () => groups.filter((group) => filter === "all" || group.type === filter),
    [filter, groups],
  );
  const totalVisible = visibleGroups.reduce((total, group) => total + group.hits.length, 0);

  function writeUrl(nextQuery: string, nextFilter: SearchFilter) {
    const params = new URLSearchParams();
    if (nextQuery) {
      params.set("q", nextQuery);
    }
    if (nextFilter !== "all") {
      params.set("type", nextFilter);
    }
    const search = params.toString();
    window.history.pushState({}, "", search ? `/search?${search}` : "/search");
  }

  function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextQuery = input.trim();
    setQuery(nextQuery);
    writeUrl(nextQuery, filter);
  }

  function chooseFilter(nextFilter: SearchFilter) {
    setFilter(nextFilter);
    writeUrl(query, nextFilter);
  }

  function selectResult(item: GlobalSearchResultItem) {
    void recordSearchSelection(query, item).catch(() => {
      // Relevance telemetry must not interrupt navigation.
    });
  }

  return (
    <div className="space-y-8">
      <form className="relative max-w-2xl" onSubmit={submit}>
        <label className="sr-only" htmlFor="catalog-search">
          Search Podex
        </label>
        <input
          className="input py-4 pl-5 pr-28 text-lg"
          id="catalog-search"
          onChange={(event) => setInput(event.target.value)}
          placeholder="Search references, episodes, or sources"
          type="search"
          value={input}
        />
        <button className="btn btn-primary absolute right-2 top-2" type="submit">
          Search
        </button>
      </form>
      <div aria-label="Search category" className="flex flex-wrap gap-2" role="group">
        {filters.map((option) => (
          <button
            aria-pressed={filter === option.id}
            className={filter === option.id ? "btn btn-primary" : "btn btn-secondary"}
            key={option.id}
            onClick={() => chooseFilter(option.id)}
            type="button"
          >
            {option.label}
          </button>
        ))}
      </div>
      {!query ? (
        <AsyncState
          title="Search the catalog"
          message="Find a reference, an episode, or a source and trace where it was discussed."
        />
      ) : loading ? (
        <AsyncState title="Searching the catalog..." variant="loading" />
      ) : error ? (
        <AsyncState title={error} variant="error" />
      ) : totalVisible === 0 ? (
        <AsyncState
          title={`No results for "${query}"`}
          message="Try another phrase or broaden the selected category."
        />
      ) : (
        <div className="space-y-8">
          {visibleGroups
            .filter((group) => group.hits.length > 0)
            .map((group) => (
              <section className="space-y-3" key={group.type}>
                <div className="flex items-baseline justify-between">
                  <h2 className="text-xl text-text">{groupLabels[group.type]}</h2>
                  <span className="text-sm text-text-muted">{group.total} found</span>
                </div>
                <div className="divide-y divide-border-subtle rounded-lg border border-border-subtle bg-surface">
                  {group.hits.map((hit) => (
                    <a
                      className="block p-4 transition-colors hover:bg-surface-elevated"
                      href={hit.url}
                      key={`${group.type}-${hit.id}`}
                      onClick={() => selectResult(hit)}
                    >
                      <p className="font-medium text-text">{hit.title}</p>
                      {hit.subtitle && (
                        <p className="mt-1 text-sm text-text-secondary">{hit.subtitle}</p>
                      )}
                    </a>
                  ))}
                </div>
              </section>
            ))}
        </div>
      )}
    </div>
  );
}
