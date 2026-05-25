interface AsyncStateProps {
  title: string;
  message?: string;
  variant?: "loading" | "empty" | "error";
}

export function AsyncState({ title, message, variant = "empty" }: AsyncStateProps) {
  const classes =
    variant === "error" ? "border-red-500/30 bg-red-500/10" : "border-border-subtle bg-surface";
  return (
    <div
      className={`rounded-lg border p-5 text-sm ${classes}`}
      role={variant === "error" ? "alert" : "status"}
    >
      <p className="font-medium text-text">{title}</p>
      {message && <p className="mt-2 text-text-secondary">{message}</p>}
    </div>
  );
}
