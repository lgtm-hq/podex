interface DegradedBannerProps {
  message?: string;
}

export default function DegradedBanner({
  message = "Unable to load podcasts right now.",
}: DegradedBannerProps) {
  return (
    <p role="alert" className="text-red-600">
      {message}
    </p>
  );
}
