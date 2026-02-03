import { useState, useEffect, useCallback } from "react";
import { Command } from "cmdk";
import { globalSearch, type GlobalSearchResultItem } from "../lib/api";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export default function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GlobalSearchResultItem[]>([]);
  const [loading, setLoading] = useState(false);

  // Search with debounce
  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(async () => {
      setLoading(true);
      try {
        const response = await globalSearch(query.trim(), 5);
        if (!controller.signal.aborted) {
          // Flatten results from all groups
          const allHits = response.results.flatMap((group) => group.hits);
          setResults(allHits);
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          console.error("Search failed:", error);
          setResults([]);
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }, 200);

    return () => {
      controller.abort();
      clearTimeout(timeoutId);
    };
  }, [query]);

  // Reset state when closing
  useEffect(() => {
    if (!open) {
      setQuery("");
      setResults([]);
    }
  }, [open]);

  const handleSelect = useCallback(
    (url: string) => {
      onOpenChange(false);
      window.location.href = url;
    },
    [onOpenChange],
  );

  const getTypeIcon = (type: string) => {
    switch (type) {
      case "media":
        return "📚";
      case "episode":
        return "🎙️";
      case "podcast":
        return "📻";
      default:
        return "🔍";
    }
  };

  const getTypeLabel = (type: string) => {
    switch (type) {
      case "media":
        return "Media";
      case "episode":
        return "Episode";
      case "podcast":
        return "Podcast";
      default:
        return type;
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-background/80 backdrop-blur-sm"
        onClick={() => onOpenChange(false)}
      />

      {/* Dialog */}
      <div className="fixed left-1/2 top-[20%] -translate-x-1/2 w-full max-w-xl">
        <Command
          className="rounded-xl border border-border bg-surface shadow-2xl overflow-hidden"
          shouldFilter={false}
          loop
        >
          {/* Input */}
          <div className="flex items-center border-b border-border px-4">
            <svg
              className="w-5 h-5 text-text-muted mr-3 shrink-0"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
              />
            </svg>
            <Command.Input
              value={query}
              onValueChange={setQuery}
              placeholder="Search media, episodes, podcasts..."
              className="flex-1 h-14 bg-transparent text-text placeholder:text-text-muted focus:outline-none"
              autoFocus
            />
            {loading && (
              <div className="w-5 h-5 border-2 border-text-muted border-t-transparent rounded-full animate-spin" />
            )}
            <button
              onClick={() => onOpenChange(false)}
              className="ml-3 p-1.5 rounded text-text-muted hover:text-text hover:bg-surface-elevated transition-colors"
            >
              <span className="kbd text-xs">ESC</span>
            </button>
          </div>

          {/* Results */}
          <Command.List className="max-h-80 overflow-y-auto p-2">
            {query && !loading && results.length === 0 && (
              <Command.Empty className="py-8 text-center text-text-muted">
                No results found for "{query}"
              </Command.Empty>
            )}

            {!query && (
              <div className="py-8 text-center text-text-muted">
                <p className="text-sm">Type to search across all content</p>
                <div className="mt-4 flex justify-center gap-6 text-xs">
                  <span>
                    <span className="kbd">↑↓</span> Navigate
                  </span>
                  <span>
                    <span className="kbd">Enter</span> Select
                  </span>
                  <span>
                    <span className="kbd">ESC</span> Close
                  </span>
                </div>
              </div>
            )}

            {results.map((item) => (
              <Command.Item
                key={`${item.type}-${item.id}`}
                value={`${item.type}-${item.id}`}
                onSelect={() => handleSelect(item.url)}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer text-text hover:bg-surface-elevated data-[selected=true]:bg-surface-elevated transition-colors"
              >
                {item.cover_url ? (
                  <img
                    src={item.cover_url}
                    alt=""
                    className="w-10 h-10 rounded object-cover shrink-0"
                  />
                ) : (
                  <div className="w-10 h-10 rounded bg-border flex items-center justify-center text-lg shrink-0">
                    {getTypeIcon(item.type)}
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <div className="font-medium truncate">{item.title}</div>
                  {item.subtitle && (
                    <div className="text-sm text-text-muted truncate">{item.subtitle}</div>
                  )}
                </div>
                <span className="text-xs text-text-muted px-2 py-1 rounded bg-surface-elevated shrink-0">
                  {getTypeLabel(item.type)}
                </span>
              </Command.Item>
            ))}
          </Command.List>
        </Command>
      </div>
    </div>
  );
}
