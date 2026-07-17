interface SkeletonListProps {
  count?: number;
}

function SkeletonItem() {
  return (
    <li
      aria-hidden="true"
      className="animate-pulse rounded-lg border border-gray-200 p-4"
    >
      <div className="mb-2 h-4 w-1/3 rounded bg-gray-200" />
      <div className="h-3 w-2/3 rounded bg-gray-100" />
    </li>
  );
}

export default function SkeletonList({ count = 4 }: SkeletonListProps) {
  return (
    <ul aria-busy="true" aria-label="Loading…" className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonItem key={i} />
      ))}
    </ul>
  );
}
