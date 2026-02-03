import { useState, useCallback } from "react";

type MediaType =
  | "book"
  | "movie"
  | "documentary"
  | "tv_show"
  | "study"
  | "podcast"
  | "article"
  | "standup_special"
  | "person"
  | "place";
type SortOption = "mention_count" | "title" | "created_at";
type SortOrder = "asc" | "desc";

interface FilterPanelProps {
  initialTypes?: MediaType[];
  initialSort?: SortOption;
  initialOrder?: SortOrder;
  onFilterChange?: (filters: FilterState) => void;
}

interface FilterState {
  types: MediaType[];
  sort: SortOption;
  order: SortOrder;
}

const mediaTypes: { value: MediaType; label: string; icon: string }[] = [
  { value: "book", label: "Books", icon: "📚" },
  { value: "movie", label: "Movies", icon: "🎬" },
  { value: "tv_show", label: "TV Shows", icon: "📺" },
  { value: "documentary", label: "Docs", icon: "🎥" },
  { value: "podcast", label: "Podcasts", icon: "🎙️" },
  { value: "standup_special", label: "Standup", icon: "🎤" },
  { value: "article", label: "Articles", icon: "📰" },
  { value: "study", label: "Studies", icon: "🔬" },
];

const sortOptions: { value: SortOption; label: string }[] = [
  { value: "mention_count", label: "Most Mentioned" },
  { value: "title", label: "Title A-Z" },
  { value: "created_at", label: "Recently Added" },
];

export default function FilterPanel({
  initialTypes = [],
  initialSort = "mention_count",
  initialOrder = "desc",
  onFilterChange,
}: FilterPanelProps) {
  const [selectedTypes, setSelectedTypes] = useState<MediaType[]>(initialTypes);
  const [sort, setSort] = useState<SortOption>(initialSort);
  const [order, setOrder] = useState<SortOrder>(initialOrder);

  const updateFilters = useCallback(
    (newState: Partial<FilterState>) => {
      const state: FilterState = {
        types: newState.types ?? selectedTypes,
        sort: newState.sort ?? sort,
        order: newState.order ?? order,
      };

      if (onFilterChange) {
        onFilterChange(state);
      } else {
        // Default behavior: update URL
        const params = new URLSearchParams();
        if (state.types.length > 0) {
          state.types.forEach((t) => params.append("type", t));
        }
        if (state.sort !== "mention_count") params.set("sort", state.sort);
        if (state.order !== "desc") params.set("order", state.order);
        const queryString = params.toString();
        window.location.href = queryString ? `/media?${queryString}` : "/media";
      }
    },
    [selectedTypes, sort, order, onFilterChange],
  );

  const toggleType = (type: MediaType) => {
    const newTypes = selectedTypes.includes(type)
      ? selectedTypes.filter((t) => t !== type)
      : [...selectedTypes, type];
    setSelectedTypes(newTypes);
    updateFilters({ types: newTypes });
  };

  const handleSortChange = (newSort: SortOption) => {
    setSort(newSort);
    updateFilters({ sort: newSort });
  };

  const toggleOrder = () => {
    const newOrder = order === "desc" ? "asc" : "desc";
    setOrder(newOrder);
    updateFilters({ order: newOrder });
  };

  const clearFilters = () => {
    setSelectedTypes([]);
    setSort("mention_count");
    setOrder("desc");
    window.location.href = "/media";
  };

  const hasActiveFilters = selectedTypes.length > 0 || sort !== "mention_count" || order !== "desc";

  return (
    <div className="space-y-4">
      {/* Type filters */}
      <div className="flex flex-wrap gap-2">
        {mediaTypes.map(({ value, label, icon }) => (
          <button
            key={value}
            onClick={() => toggleType(value)}
            className={`inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-all ${
              selectedTypes.includes(value)
                ? "bg-text text-background"
                : "bg-surface border border-border-subtle text-text-secondary hover:border-border hover:text-text"
            }`}
          >
            <span>{icon}</span>
            <span>{label}</span>
          </button>
        ))}
      </div>

      {/* Sort controls */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs font-medium uppercase tracking-wider text-text-muted">
          Sort by
        </span>

        <div className="flex items-center gap-2">
          <select
            value={sort}
            onChange={(e) => handleSortChange(e.target.value as SortOption)}
            className="input py-2 pr-8 text-sm appearance-none cursor-pointer"
            style={{
              backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
              backgroundPosition: "right 0.5rem center",
              backgroundRepeat: "no-repeat",
              backgroundSize: "1.5em 1.5em",
            }}
          >
            {sortOptions.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>

          <button
            onClick={toggleOrder}
            className="p-2 rounded-lg bg-surface border border-border-subtle text-text-muted hover:text-text hover:border-border transition-all"
            title={order === "desc" ? "Descending (highest first)" : "Ascending (lowest first)"}
          >
            <svg
              className={`w-4 h-4 transition-transform duration-200 ${order === "asc" ? "rotate-180" : ""}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M19 9l-7 7-7-7"
              />
            </svg>
          </button>
        </div>

        {hasActiveFilters && (
          <button
            onClick={clearFilters}
            className="text-xs font-medium text-text-muted hover:text-text transition-colors ml-auto"
          >
            Clear filters
          </button>
        )}
      </div>
    </div>
  );
}
